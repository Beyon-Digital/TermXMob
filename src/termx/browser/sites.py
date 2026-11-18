"""Saved sites: pinned origins with per-site notes and a decayed usage score (frecency).

Rows live in real tables of ``browser.sqlite3`` (see ``storage.SITES_SCHEMA``). Every query is scoped by principal,
exactly like the browser's JSON records, so one user's sites, notes and counters are never visible to another.

Frecency is an exponentially decayed visit count with a 14 day half-life. The stored ``rank`` is
``log2(score) + last_visited / half_life``; ordering by it is the same as ordering by the decayed score at any later
instant, so the sidebar query needs only an index, never a rescan that recomputes decay.
"""
from __future__ import annotations
import math
import re
import secrets
import unicodedata
from time import time
from urllib.parse import urlsplit,urlunsplit
from termx.browser.network import origin as origin_of,TargetDenied
from termx.browser.storage import Records

HALF_LIFE=14*86400.0
VISIT_GAP=300.0 # Navigations inside one browsing session of an origin count as a single visit.
MAX_SITES=200
MAX_CANDIDATES=300 # Unsaved origins whose visits are counted per user; the lowest ranked are dropped beyond this.
SUGGEST_MIN_VISITS=3
MAX_NOTES=50
NOTE_MAX=2000
TITLE_MAX=200
URL_MAX=500
OPEN_MODES=('tab','app')
AUTHORS=('user','agent')
SITE_COLUMNS='id,project_id,origin,start_url,title,profile_id,open_mode,pinned,position,visit_count,last_visited,frecency,rank,tab_id,created,updated'
# Dropped from text that other agents will read back: control characters, bidirectional overrides, zero-width marks.
_HIDDEN=re.compile('[​-‏‪-‮⁠-⁤⁦-⁩﻿]')

def clean_text(value,limit,*,multiline=False,required=False,field='text'):
    """Normalise untrusted text: no control, bidi-override or zero-width characters; over-long input is refused."""
    text=unicodedata.normalize('NFC',str(value if value is not None else ''))
    text=_HIDDEN.sub('',text)
    allowed='\n\t' if multiline else ''
    text=''.join(ch for ch in text if ch in allowed or (unicodedata.category(ch)[0]!='C' and ch not in '  '))
    text=text.strip() if multiline else ' '.join(text.split())
    if required and not text:raise ValueError(field+' is required')
    if len(text)>limit:raise ValueError(f'{field} must be at most {limit} characters')
    return text

def site_url(url):
    """Start URL and origin for a saved site. Queries and fragments are dropped: they carry tokens and session state."""
    if not isinstance(url,str) or not url.strip() or len(url)>2048:raise ValueError('a site URL is required')
    parts=urlsplit(url.strip())
    if parts.scheme not in {'http','https'}:raise ValueError('only HTTP(S) sites can be saved')
    try:origin=origin_of(urlunsplit((parts.scheme,parts.netloc,'','','')))
    except TargetDenied as error:raise ValueError(str(error)) from error
    path=parts.path or '/'
    start=origin+path
    if len(start)>URL_MAX:raise ValueError(f'site URL must be at most {URL_MAX} characters')
    return start,origin

def decayed(score,since,now):
    return score*2.0**(-max(0.0,now-since)/HALF_LIFE) if since else 0.0

def rank_of(score,at):
    return math.log2(score)+at/HALF_LIFE if score>0 else 0.0

def current_frecency(rank,now):
    return 2.0**(rank-now/HALF_LIFE) if rank else 0.0

def public(row,notes=0,*,now=None):
    site=dict(zip(SITE_COLUMNS.split(','),row[:16]))
    now=time() if now is None else now
    site['pinned']=bool(site['pinned'])
    site['frecency']=round(current_frecency(site.pop('rank'),now),4) if site['last_visited'] else 0.0
    site['note_count']=notes
    return site

# Fields whose change makes a site "upserted"; the visit counters alone make it "visited". The decayed score changes
# continuously and is therefore not compared.
_VISIT_FIELDS=('visit_count','last_visited','frecency')

def diff_sites(previous,current):
    """Poll-and-diff events between two ``{id: site}`` snapshots (``browser.site.*``)."""
    events=[]
    for id,site in current.items():
        before=previous.get(id)
        if before is None or any(before[key]!=site[key] for key in site if key not in _VISIT_FIELDS):
            events.append({'type':'browser.site.upserted','id':id,'site':site})
        elif any(before[key]!=site[key] for key in ('visit_count','last_visited')):
            events.append({'type':'browser.site.visited','id':id,'site':site})
    for id in previous.keys()-current.keys():events.append({'type':'browser.site.removed','id':id})
    return events

class SiteStore:
    def __init__(self,records:Records):self.records=records
    def _owned(self,db,id,principal):
        row=db.execute(f'SELECT {SITE_COLUMNS} FROM sites WHERE id=? AND principal_id=?',(id,principal)).fetchone()
        if not row:raise KeyError('site not found')
        return row
    def _notes(self,db,id):return db.execute('SELECT COUNT(*) FROM site_notes WHERE site_id=?',(id,)).fetchone()[0]
    def list(self,principal,*,profile_id=None,pinned=None,limit=MAX_SITES,now=None):
        where='principal_id=?';args=[principal]
        if profile_id is not None:where+=' AND profile_id=?';args.append(profile_id)
        if pinned is not None:where+=' AND pinned=?';args.append(1 if pinned else 0)
        with self.records.transaction() as db:
            rows=db.execute(f'SELECT {SITE_COLUMNS},(SELECT COUNT(*) FROM site_notes WHERE site_id=sites.id) FROM sites WHERE {where} ORDER BY pinned DESC,CASE WHEN pinned=1 THEN position ELSE 0 END,rank DESC,created LIMIT ?',(*args,max(1,min(int(limit),MAX_SITES))),).fetchall()
        return [public(row[:16],row[16],now=now) for row in rows]
    def get(self,id,principal,*,now=None):
        with self.records.transaction() as db:
            row=self._owned(db,id,principal);return public(row,self._notes(db,id),now=now)
    def save(self,principal,profile_id,project_id,url,*,title='',open_mode='tab',pinned=True,now=None):
        """Create the site for this user, profile and origin, or update it when it exists. Returns ``(site, created)``."""
        start,origin=site_url(url)
        if open_mode not in OPEN_MODES:raise ValueError('open_mode must be tab or app')
        title=clean_text(title,TITLE_MAX,field='title');now=time() if now is None else now
        with self.records.transaction() as db:
            existing=db.execute('SELECT id FROM sites WHERE principal_id=? AND profile_id=? AND origin=?',(principal,profile_id,origin)).fetchone()
            if existing:
                db.execute('UPDATE sites SET start_url=?,title=CASE WHEN ?<>\'\' THEN ? ELSE title END,open_mode=?,pinned=?,updated=? WHERE id=?',(start,title,title,open_mode,1 if pinned else 0,now,existing[0]))
                row=self._owned(db,existing[0],principal);return public(row,self._notes(db,existing[0]),now=now),False
            if db.execute('SELECT COUNT(*) FROM sites WHERE principal_id=?',(principal,)).fetchone()[0]>=MAX_SITES:raise ValueError(f'at most {MAX_SITES} saved sites')
            seen=db.execute('SELECT title,visit_count,last_visited,frecency,rank FROM site_visits WHERE principal_id=? AND profile_id=? AND origin=?',(principal,profile_id,origin)).fetchone()
            db.execute('DELETE FROM site_visits WHERE principal_id=? AND profile_id=? AND origin=?',(principal,profile_id,origin))
            position=db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM sites WHERE principal_id=? AND pinned=1',(principal,)).fetchone()[0]
            id=secrets.token_urlsafe(12)
            title=title or (seen[0] if seen else '') or origin.split('://',1)[1]
            db.execute('INSERT INTO sites(id,principal_id,project_id,origin,start_url,title,profile_id,open_mode,pinned,position,visit_count,last_visited,frecency,rank,tab_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)',
                       (id,principal,project_id or '',origin,start,title,profile_id,open_mode,1 if pinned else 0,position,*(seen[1:] if seen else (0,None,0.0,0.0)),now,now))
            return public(self._owned(db,id,principal),0,now=now),True
    def update(self,id,principal,changes,*,now=None):
        now=time() if now is None else now;sets=[];args=[]
        with self.records.transaction() as db:
            row=self._owned(db,id,principal)
            for key,value in changes.items():
                if key=='title':sets.append('title=?');args.append(clean_text(value,TITLE_MAX,required=True,field='title'))
                elif key=='open_mode':
                    if value not in OPEN_MODES:raise ValueError('open_mode must be tab or app')
                    sets.append('open_mode=?');args.append(value)
                elif key=='pinned':
                    sets.append('pinned=?');args.append(1 if value else 0)
                    if value and not row[7]:
                        sets.append('position=?');args.append(db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM sites WHERE principal_id=? AND pinned=1',(principal,)).fetchone()[0])
                elif key=='position':
                    if not isinstance(value,int) or isinstance(value,bool) or not 0<=value<=100000:raise ValueError('position must be a non-negative integer')
                    sets.append('position=?');args.append(value)
                elif key=='start_url':
                    start,origin=site_url(value)
                    if origin!=row[2]:raise ValueError('start_url must stay on the saved origin')
                    sets.append('start_url=?');args.append(start)
                else:raise ValueError('unsupported site field: '+str(key))
            if sets:db.execute(f'UPDATE sites SET {",".join(sets)},updated=? WHERE id=?',(*args,now,id))
            return public(self._owned(db,id,principal),self._notes(db,id),now=now)
    def delete(self,id,principal):
        with self.records.transaction() as db:
            row=self._owned(db,id,principal)
            db.execute('DELETE FROM site_notes WHERE site_id=?',(id,))
            db.execute('DELETE FROM sites WHERE id=?',(id,))
            # A removed site starts counting from nothing again instead of being re-suggested at once.
            db.execute('DELETE FROM site_visits WHERE principal_id=? AND profile_id=? AND origin=?',(principal,row[5],row[2]))
        return {'id':id}
    def set_tab(self,id,principal,tab_id,*,now=None):
        with self.records.transaction() as db:
            self._owned(db,id,principal)
            db.execute('UPDATE sites SET tab_id=?,updated=? WHERE id=?',(tab_id,time() if now is None else now,id))
            return public(self._owned(db,id,principal),self._notes(db,id),now=now)
    def touch(self,principal,profile_id,project_id,origin,title,*,now=None):
        """Record one navigation to ``origin``. Returns ``{site_id, saved, counted, visit_count}``.

        Saved sites keep their own counters (visits never touch ``updated``, which marks edits); any other origin gets a bounded counter that feeds suggestions.
        """
        now=time() if now is None else now;title=clean_text(title,TITLE_MAX,field='title')
        with self.records.transaction() as db:
            saved=db.execute('SELECT id,visit_count,last_visited,frecency FROM sites WHERE principal_id=? AND profile_id=? AND origin=?',(principal,profile_id,origin)).fetchone()
            if saved:
                id,count,last,score=saved;counted=not last or now-last>=VISIT_GAP
                if counted:
                    score=decayed(score,last,now)+1;count+=1
                    db.execute('UPDATE sites SET visit_count=?,last_visited=?,frecency=?,rank=? WHERE id=?',(count,now,score,rank_of(score,now),id))
                else:db.execute('UPDATE sites SET last_visited=? WHERE id=?',(now,id))
                return {'site_id':id,'saved':True,'counted':counted,'visit_count':count}
            seen=db.execute('SELECT visit_count,last_visited,frecency FROM site_visits WHERE principal_id=? AND profile_id=? AND origin=?',(principal,profile_id,origin)).fetchone()
            count,last,score=seen or (0,0.0,0.0);counted=not seen or now-last>=VISIT_GAP
            if counted:score=decayed(score,last,now)+1;count+=1
            db.execute('INSERT INTO site_visits(principal_id,profile_id,origin,title,project_id,visit_count,last_visited,frecency,rank) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(principal_id,profile_id,origin) DO UPDATE SET title=CASE WHEN excluded.title<>\'\' THEN excluded.title ELSE title END,visit_count=excluded.visit_count,last_visited=excluded.last_visited,frecency=excluded.frecency,rank=excluded.rank',
                       (principal,profile_id,origin,title,project_id or '',count,now,score,rank_of(score,now)))
            if not seen and db.execute('SELECT COUNT(*) FROM site_visits WHERE principal_id=?',(principal,)).fetchone()[0]>MAX_CANDIDATES:
                db.execute('DELETE FROM site_visits WHERE principal_id=? AND (profile_id,origin) NOT IN (SELECT profile_id,origin FROM site_visits WHERE principal_id=? ORDER BY rank DESC LIMIT ?)',(principal,principal,MAX_CANDIDATES))
            return {'site_id':None,'saved':False,'counted':counted,'visit_count':count}
    def suggestions(self,principal,*,limit=5,minimum=SUGGEST_MIN_VISITS,now=None):
        now=time() if now is None else now
        with self.records.transaction() as db:
            rows=db.execute('SELECT profile_id,origin,title,project_id,visit_count,last_visited,rank FROM site_visits WHERE principal_id=? AND visit_count>=? AND NOT EXISTS(SELECT 1 FROM sites WHERE sites.principal_id=site_visits.principal_id AND sites.profile_id=site_visits.profile_id AND sites.origin=site_visits.origin) ORDER BY rank DESC LIMIT ?',(principal,max(1,minimum),max(1,min(int(limit),50)))).fetchall()
        return [{'profile_id':r[0],'origin':r[1],'start_url':r[1]+'/','title':r[2] or r[1].split('://',1)[1],'project_id':r[3],'visit_count':r[4],'last_visited':r[5],'frecency':round(current_frecency(r[6],now),4)} for r in rows]
    def notes(self,id,principal):
        with self.records.transaction() as db:
            self._owned(db,id,principal)
            return [{'id':r[0],'site_id':id,'author':r[1],'content':r[2],'created':r[3],'updated':r[4]} for r in db.execute('SELECT id,author,content,created,updated FROM site_notes WHERE site_id=? ORDER BY created,id',(id,))]
    def add_note(self,id,principal,author,content,*,now=None):
        if author not in AUTHORS:raise ValueError('author must be user or agent')
        content=clean_text(content,NOTE_MAX,multiline=True,required=True,field='note');now=time() if now is None else now
        with self.records.transaction() as db:
            self._owned(db,id,principal)
            if self._notes(db,id)>=MAX_NOTES:raise ValueError(f'at most {MAX_NOTES} notes per site')
            note=secrets.token_urlsafe(12)
            db.execute('INSERT INTO site_notes(id,site_id,author,content,created,updated) VALUES(?,?,?,?,?,?)',(note,id,author,content,now,now))
            db.execute('UPDATE sites SET updated=? WHERE id=?',(now,id))
        return {'id':note,'site_id':id,'author':author,'content':content,'created':now,'updated':now}
    def delete_note(self,id,principal,note_id,*,now=None):
        with self.records.transaction() as db:
            self._owned(db,id,principal)
            if not db.execute('DELETE FROM site_notes WHERE id=? AND site_id=?',(note_id,id)).rowcount:raise KeyError('note not found')
            db.execute('UPDATE sites SET updated=? WHERE id=?',(time() if now is None else now,id))
        return {'id':note_id}
    def clear_profile(self,profile_id,*,remove=False):
        """Clearing a profile forgets what was visited; removing it also removes the sites and notes saved on it."""
        with self.records.transaction() as db:
            db.execute('DELETE FROM site_visits WHERE profile_id=?',(profile_id,))
            if remove:
                db.execute('DELETE FROM site_notes WHERE site_id IN (SELECT id FROM sites WHERE profile_id=?)',(profile_id,))
                db.execute('DELETE FROM sites WHERE profile_id=?',(profile_id,))
            else:db.execute('UPDATE sites SET visit_count=0,last_visited=NULL,frecency=0,rank=0,tab_id=NULL WHERE profile_id=?',(profile_id,))
