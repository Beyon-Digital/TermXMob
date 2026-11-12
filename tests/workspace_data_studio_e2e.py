"""Data Studio actual-browser/real-host proof with an explicit synthetic business SQLite file.

.venv/bin/python -B tests/workspace_data_studio_e2e.py OUTPUT UI_DIST
No real profile, production database, external inference, deployment or invitations.
"""
from __future__ import annotations
import asyncio,csv,hashlib,io,json,os,re,shutil,sqlite3,sys,tempfile,threading,uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from playwright.sync_api import expect,sync_playwright
REPO=Path(os.environ.get('TERMX_DATA_QA_REPO')or(Path(__file__).resolve().parents[1]if Path(__file__).parent.name=='tests'else Path.cwd()))
sys.path.insert(0,str(REPO/'tests'))
import workspace_studio_projects_e2e as p
from workspace_studio_composer_refinement_e2e import focus_sample as shared_focus_sample

def focus_sample(page,element,label,report,strict):
    """The shell paints Data focus rings with a 2px offset (DESIGN.md), so the shared inset-only rule is not applied here.
    Everything else the shared check enforces still is: a visible ring, exactly one, no clipping, and 44px targets on touch."""
    value=shared_focus_sample(page,element,label,report,False)
    if strict:
        if page.viewport_size['width']<640:
            rect=value['hitTarget'];assert rect['height']>=44 and rect['width']>=44,('Coarse target below 44px',label,rect)
        assert value['focusVisible'],value
        assert value['ring_count']==1,'Expected one painted focus ring: '+json.dumps(value)
        assert not value['clipping'],'Focus marker is clipped/overflowing: '+json.dumps(value)
    return value

SECOND='synthetic-data-member'
ROWS=2501
TOTAL=ROWS*(ROWS+1)//2
SQL='SELECT id, region, amount, day FROM sales ORDER BY id'
LONG_SQL='WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<100000000) SELECT sum(x) FROM c'
EXPECTED={'East':1565001,'West':1563750}
KEY='synthetic-data-memory-only-key'

def request(api,method,path,body=None,code=200):
    response=api.request(method,path,json=body) if body is not None else api.request(method,path)
    assert response.status_code==code,(method,path,response.status_code,response.text)
    return response.json() if 'application/json'in response.headers.get('content-type','') else response

def denied(api,method,path,body=None,codes=(403,404,409)):
    response=api.request(method,path,json=body) if body is not None else api.request(method,path)
    assert response.status_code in codes,(method,path,response.status_code,response.text)
    return response.status_code

def login_api(base,username=p.USERNAME):
    raw=httpx.post(base+'/auth/login',json={'method':'local-password','username':username,'password':p.PASSWORD,'transport':'bearer','device_name':'Synthetic Data QA probe'},trust_env=False)
    assert raw.status_code==200,raw.text
    return httpx.Client(base_url=base,headers={'Authorization':'Bearer '+raw.json()['access_token']},trust_env=False)

def authenticate(page,base,username=p.USERNAME,target='/?workspace=data'):
    page.goto(base+target)
    page.get_by_label('Username',exact=True).fill(username);page.get_by_label('Password',exact=True).fill(p.PASSWORD)
    page.get_by_role('button',name='Continue with password',exact=True).click()
    expect(page.get_by_role('region',name='Data Studio',exact=True)).to_be_visible(timeout=60000)

def protect_context(context,base,report):
    origin=urlsplit(base)
    def network(route):
        req=route.request;url=urlsplit(req.url)
        if url.scheme in('http','https')and(url.scheme,url.netloc)!=(origin.scheme,origin.netloc):
            report['external_browser_requests'].append(req.url);route.abort('blockedbyclient');return
        if req.method=='POST'and(re.match(r'/api/(?:agent/tasks|workspace/sessions/[^/]+/turns)',url.path)or re.match(r'/api/media/studio/(?:music/)?jobs',url.path)):
            raise AssertionError('Unexpected non-Data dispatch')
        route.continue_()
    context.route('**/*',network)

def capture(page,out,report,name,theme,width,expect_alert=False):
    item=p.audit(page,out,report,name,theme,width,REPO/'desktop/workspace/node_modules/axe-core/axe.min.js')
    if expect_alert:assert item['alerts'],('Expected the visible notice for this state',name)
    else:assert not item['alerts'],('Unexpected visible error in Data happy path',name,item['alerts'])
    item['data_geometry']=page.locator('.data-studio').evaluate("e=>({rect:e.getBoundingClientRect().toJSON(),computed:{background:getComputedStyle(e).backgroundColor,color:getComputedStyle(e).color},coarse:matchMedia('(pointer:coarse)').matches,reduced:matchMedia('(prefers-reduced-motion:reduce)').matches})")
    return item

def chart_geometry(chart,page,report,theme,width):
    def measure():
        return chart.evaluate("e=>{const r=e.getBoundingClientRect(),v=e.viewBox.baseVal,scale=Math.min(r.width/v.width,r.height/v.height),labels=[...e.querySelectorAll('.bi-chart-label')].map(t=>({text:t.textContent,font:parseFloat(getComputedStyle(t).fontSize),rect:t.getBoundingClientRect().toJSON()}));return{rect:r.toJSON(),viewBox:{width:v.width,height:v.height},scale,labels,min_effective_font:Math.min(...labels.map(t=>t.font*scale))}}")
    p.wait(lambda:measure()['min_effective_font']>=11.5,page=page,seconds=10)
    value=measure();assert value['labels']and value['min_effective_font']>=11.5,value
    assert abs(value['rect']['width']-value['viewBox']['width'])<=2,value
    report.setdefault('chart_geometry',[]).append({'theme':theme,'width':width,**value})
    return value

def side(page):
    navigation=page.locator('#workspace-sidebar')
    show=page.get_by_role('button',name='Show sidebar',exact=True)
    if show.count():show.click()
    expect(navigation).to_be_visible();return navigation

def hide_side(page):
    """Below 850px the sidebar is a drawer over the page; close it after using controls that do not navigate."""
    hide=page.get_by_role('button',name='Hide sidebar',exact=True)
    if page.viewport_size['width']<=850 and hide.count():hide.click();expect(page.locator('#workspace-sidebar')).not_to_be_visible()

def nav(page,name):
    """Reports and Members are sidebar rows; the table overview is the "All tables" row."""
    navigation=side(page)
    if name=='BI':navigation.get_by_role('navigation',name='Reports',exact=True).get_by_role('button').first.click()
    elif name=='Team':navigation.get_by_role('button',name='Members',exact=True).click()
    else:navigation.get_by_role('button',name='All tables',exact=True).click()

def set_sql(page,text):
    editor=page.locator('#data-sql');expect(editor).to_be_visible();editor.click()
    page.keyboard.press('ControlOrMeta+a');page.keyboard.insert_text(text)
    expect(editor).to_contain_text(text.split('\n')[0][:40]);return editor

def open_advanced(page):
    page.get_by_role('button',name='Advanced',exact=True).click()
    page.get_by_role('menuitemcheckbox',name='SQL editor',exact=True).click()
    panel=page.get_by_role('dialog',name='SQL editor',exact=True);expect(panel).to_be_visible()
    # The editor takes focus, not the menu button, so the keyboard path continues inside the panel.
    expect(page.locator('#data-sql')).to_be_focused();return panel

def close_advanced(page,panel):
    page.keyboard.press('Escape');expect(panel).not_to_be_visible()
    expect(page.get_by_role('button',name='Advanced',exact=True)).to_be_focused()

def rows_of(grid):
    return [row.get_by_role('cell').all_inner_texts() for row in grid.get_by_role('row').all()[1:]]

def expected_by_region(path):
    with sqlite3.connect(path)as db:
        return dict(db.execute('SELECT r.name, SUM(s.amount) FROM sales s JOIN customers c ON s.customer_id=c.id JOIN regions r ON c.region_id=r.id GROUP BY r.name'))

def visual_journey(page,data,path,out,report,theme,width):
    """Browse, sort, filter, combine and summarize three related tables without writing any SQL."""
    side(page).get_by_role('navigation',name='Tables',exact=True).get_by_role('button',name='sales',exact=True).click()
    tab=data.get_by_role('region',name='Table sales',exact=True);grid=tab.get_by_role('table',name='Result table',exact=True)
    expect(tab.get_by_text('Rows 1 to 200',exact=True)).to_be_visible();expect(grid.get_by_role('row').nth(1)).to_be_visible()
    expect(tab.get_by_role('button',name='Show SQL',exact=True)).to_be_visible()
    capture(page,out,report,'table-grid-default',theme,width)
    clipped=page.evaluate("[...document.querySelectorAll('.data-grid-label')].filter(e=>e.scrollWidth>e.clientWidth).map(e=>[e.textContent,e.scrollWidth,e.clientWidth,e.closest('.data-grid-cell').getBoundingClientRect().width,getComputedStyle(e.closest('.data-grid-cell')).padding,getComputedStyle(e.closest('.data-grid-cell')).gap,e.parentElement.getBoundingClientRect().width,innerWidth])")
    assert not clipped,('Grid header labels are clipped',clipped)
    keys=[]
    tab.get_by_role('button',name='Combine with another table',exact=True).focus()
    for _ in range(5):
        keys.append(page.evaluate("document.activeElement.textContent.trim()"));page.keyboard.press('Tab')
    assert keys==['Combine with another table','Summarize','Columns','Show SQL','Export CSV'],keys
    # Sorting from the header, descending on the second click.
    amount=tab.get_by_role('button',name='amount',exact=True);amount.click();amount.click()
    expect(tab.get_by_role('columnheader',name=re.compile('^amount'))).to_have_attribute('aria-sort','descending')
    expect(grid.get_by_role('row').nth(1).get_by_role('cell').nth(2)).to_have_text(str(ROWS))
    # Filters: from the column popover and from "Add filter", shown as sentences.
    tab.get_by_role('button',name='Filter region',exact=True).click()
    popover=page.get_by_role('dialog',name='Filter region',exact=True)
    popover.get_by_label('Condition',exact=True).select_option('eq');popover.get_by_label('Value',exact=True).fill('East');popover.get_by_role('button',name='Apply',exact=True).click()
    expect(tab.get_by_role('button',name='Region is East',exact=True)).to_be_visible()
    tab.get_by_role('button',name='Add filter',exact=True).click()
    adding=page.get_by_role('dialog',name='Add filter',exact=True)
    adding.get_by_label('Column',exact=True).select_option(label='Amount');adding.get_by_label('Condition',exact=True).select_option('gt');adding.get_by_label('Value',exact=True).fill('2400');adding.get_by_role('button',name='Apply',exact=True).click()
    expect(tab.get_by_role('button',name='Amount is greater than 2400',exact=True)).to_be_visible()
    expect(tab.get_by_text('Rows 1 to 51 of 51',exact=True)).to_be_visible()
    capture(page,out,report,'filter-chips-match-all',theme,width)
    tab.get_by_label('Match mode',exact=True).select_option('or');expect(tab.get_by_text('Rows 1 to 200',exact=True)).to_be_visible()
    capture(page,out,report,'filter-chips-match-any',theme,width)
    tab.get_by_label('Match mode',exact=True).select_option('and');expect(tab.get_by_text('Rows 1 to 51 of 51',exact=True)).to_be_visible()
    tab.get_by_role('button',name='Amount is greater than 2400',exact=True).focus();page.keyboard.press('Delete')
    expect(tab.get_by_role('button',name='Amount is greater than 2400',exact=True)).to_have_count(0)
    tab.get_by_role('button',name='Remove filter: Region is East',exact=True).click()
    expect(tab.get_by_text('Rows 1 to 200',exact=True)).to_be_visible()
    # Combine: a suggested link, then a link chosen by hand.
    tab.get_by_role('button',name='Combine with another table',exact=True).click()
    combine=page.get_by_role('dialog',name='Combine with another table',exact=True)
    combine.get_by_role('button',name=re.compile('^Add: Sales to Customers')).click()
    joined=tab.get_by_role('list',name='Combined tables',exact=True);expect(joined).to_contain_text('Combined with Customers: customer_id matches id')
    tab.get_by_role('button',name='Combine with another table',exact=True).click()
    add=combine.get_by_role('button',name='Add table',exact=True);box=add.bounding_box();viewport=page.viewport_size
    assert box and box['y']>=0 and box['y']+box['height']<=viewport['height'],('The primary action is below the fold',box,viewport)
    if viewport['width']<640:
        assert box['height']>=44,('Touch target below 44px in a popover',box)
        assert combine.get_by_label('Table to combine',exact=True).bounding_box()['height']>=44
    combine.get_by_label('Table to combine',exact=True).select_option('regions');combine.get_by_label('This column',exact=True).select_option(label='Customers · region_id')
    combine.get_by_label('Matches this column',exact=True).select_option('id');combine.get_by_role('button',name='Add table',exact=True).click()
    expect(joined).to_contain_text('Combined with Regions: region_id matches id')
    expect(tab.get_by_role('columnheader',name=re.compile('^regions.name'))).to_be_visible()
    tab.get_by_role('button',name='Filter regions.name',exact=True).click()
    popover=page.get_by_role('dialog',name='Filter regions.name',exact=True)
    popover.get_by_label('Condition',exact=True).select_option('eq');popover.get_by_label('Value',exact=True).fill('North');popover.get_by_role('button',name='Apply',exact=True).click()
    expect(tab.get_by_role('button',name='Regions · Name is North',exact=True)).to_be_visible()
    expect(grid.get_by_role('row').nth(1)).to_contain_text('North')
    capture(page,out,report,'combined-three-tables',theme,width)
    tab.get_by_role('button',name='Remove filter: Regions · Name is North',exact=True).click()
    # Summarize: total of amount by region name, checked against the file itself.
    tab.get_by_role('button',name='Summarize',exact=True).click()
    summary=tab.get_by_role('region',name='Summarize',exact=True)
    summary.get_by_label('Measure',exact=True).select_option('sum');summary.get_by_label('Column',exact=True).select_option(label='Sales · Amount');summary.get_by_label('Group by',exact=True).select_option(label='Regions · Name')
    expect(grid.get_by_role('row')).to_have_count(5)
    got={cells[0]:int(cells[1]) for cells in rows_of(grid)};assert got==expected_by_region(path),(got,expected_by_region(path))
    capture(page,out,report,'summarize-by-region',theme,width)
    summary.get_by_label('Group by',exact=True).select_option(label='Sales · Day');summary.get_by_label('Date bucket',exact=True).select_option('month')
    expect(grid.get_by_role('row')).to_have_count(2)
    assert rows_of(grid)==[['2026-01',str(TOTAL)]],rows_of(grid)
    # Show SQL is read-only text; the summary exports as CSV.
    tab.get_by_role('button',name='Show SQL',exact=True).click()
    generated=tab.get_by_role('region',name='Generated SQL',exact=True);expect(generated).to_contain_text('SELECT');expect(generated).to_contain_text('"sales"')
    assert generated.locator('pre').get_attribute('contenteditable')is None and generated.locator('textarea').count()==0
    capture(page,out,report,'show-sql',theme,width)
    with page.expect_download()as exporting:tab.get_by_role('button',name='Export CSV',exact=True).click()
    exported=list(csv.reader(io.StringIO(Path(exporting.value.path()).read_text())));assert len(exported)==2 and exported[1][1]==str(TOTAL),exported
    summary.get_by_role('button',name='Clear summary',exact=True).click()
    expect(tab.get_by_text('Rows 1 to 200',exact=True)).to_be_visible()
    report['checks'].append({'check':'actual-sqlite-visual-query-journey','theme':theme,'width':width,'tables':['sales','customers','regions'],'sorted':True,'filter_chips_all_any':True,'joins':2,'summary_by_region':expected_by_region(path),'summary_total':TOTAL,'show_sql_read_only':True,'csv_rows':len(exported)})


MONTH='2026-01'
SEGMENT='Big sales'
PIVOT='Sales by region and month'

def oracle_pivot(path):
    """Region by month totals computed straight from the file, independent of the app."""
    with sqlite3.connect(path)as db:
        return {region:int(total) for region,total in db.execute("SELECT region, SUM(amount) FROM sales WHERE substr(day,1,7)=? GROUP BY region ORDER BY region",(MONTH,))}

def oracle_count(path,where,params=()):
    with sqlite3.connect(path)as db:return db.execute('SELECT COUNT(*) FROM sales WHERE '+where,params).fetchone()[0]

def pivot_rows(grid):
    """[[row label, number, ...]] read from the pivot grid (row headers first)."""
    return grid.locator('tbody tr').evaluate_all("rows=>rows.map(r=>[...r.children].map(c=>c.textContent.trim()))")

def tab_named(data,pattern):
    return data.get_by_role('tablist',name='Open tables and queries',exact=True).get_by_role('tab',name=re.compile(pattern)).last

def segment_button(page):
    return side(page).get_by_role('navigation',name='Segments',exact=True).get_by_role('button',name=re.compile('^'+SEGMENT))

def views_api(api,prefix):
    return {row['name']:row for row in request(api,'GET',prefix+'/views')['views']}

def add_amount_filter(page,tab,condition,value):
    tab.get_by_role('button',name='Add filter',exact=True).click()
    adding=page.get_by_role('dialog',name='Add filter',exact=True)
    adding.get_by_label('Column',exact=True).select_option(label='Amount');adding.get_by_label('Condition',exact=True).select_option(condition);adding.get_by_label('Value',exact=True).fill(value)
    adding.get_by_role('button',name='Apply',exact=True).click()

def pivot_journey(page,data,api,path,prefix,cid,out,report,theme,width):
    """Build a pivot (rows region, columns month, value total of amount), drill into a cell, save it as a view, then drill through the saved view."""
    pivots=side(page).get_by_role('navigation',name='Pivots',exact=True)
    pivots.get_by_role('button',name='New pivot',exact=True).click()
    chooser=page.get_by_role('dialog',name='New pivot',exact=True);expect(chooser.get_by_role('button',name='sales',exact=True)).to_be_visible()
    chooser.get_by_role('button',name='sales',exact=True).click();hide_side(page)
    tab=data.get_by_role('region',name='Pivot New pivot on Sales',exact=True);expect(tab).to_be_visible()
    setup=tab.get_by_role('region',name='Pivot setup',exact=True)
    expect(setup.get_by_role('group',name='Rows',exact=True).get_by_label('Row',exact=True)).to_have_value(re.compile('region'))
    expect(tab.get_by_role('button',name='Save view',exact=True)).to_be_visible()
    columns=setup.get_by_role('group',name='Columns',exact=True);columns.get_by_label('Column',exact=True).select_option(label='Day')
    expect(columns.get_by_label('Columns by',exact=True)).to_have_value('month')
    value=setup.get_by_role('group',name='Value',exact=True);value.get_by_label('Show',exact=True).select_option('sum')
    expect(value.get_by_label('Column',exact=True).locator('option:checked')).to_have_text('Amount')
    grid=tab.get_by_role('grid',name='New pivot on Sales',exact=True);expect(grid).to_be_visible()
    expect(grid.get_by_role('columnheader')).to_have_count(3)
    expected=oracle_pivot(path);assert expected==EXPECTED,expected
    expect(grid.locator('tbody tr')).to_have_count(2)
    shown=pivot_rows(grid);assert [r[0]for r in shown]==['East','West'],shown
    assert [[int(c.replace(',',''))for c in r[1:]]for r in shown]==[[expected[r[0]],expected[r[0]]]for r in shown],(shown,expected)
    assert grid.get_by_role('columnheader').all_inner_texts()[1]==MONTH,grid.get_by_role('columnheader').all_inner_texts()
    capture(page,out,report,'pivot-builder-unsaved',theme,width)
    # Drill into the East cell of the month. The pivot is not saved yet, so the rows are opened from the visible definition.
    east=grid.locator('tbody tr').first.get_by_role('gridcell').first;east.focus();page.keyboard.press('Enter')
    drilled=data.get_by_role('region',name=f'Table East · {MONTH}',exact=True);expect(drilled).to_be_visible()
    expect(drilled.get_by_text('Rows 1 to 200',exact=True)).to_be_visible()
    drilled.get_by_role('button',name='Summarize',exact=True).click()
    summary=drilled.get_by_role('region',name='Summarize',exact=True);summary.get_by_label('Measure',exact=True).select_option('count')
    east_rows=oracle_count(path,"region='East' AND day>=? AND day<?",(MONTH+'-01','2026-02-01'))
    result=drilled.get_by_role('table',name='Result table',exact=True);expect(result.get_by_role('row')).to_have_count(2)
    assert rows_of(result)==[[str(east_rows)]],rows_of(result)
    capture(page,out,report,'pivot-drill-unsaved',theme,width)
    summary.get_by_role('button',name='Clear summary',exact=True).click();expect(drilled.get_by_text('Rows 1 to 200',exact=True)).to_be_visible()
    # Save the pivot as a view.
    tab_named(data,'New pivot on sales').click();expect(tab).to_be_visible()
    tab.get_by_role('button',name='Save view',exact=True).click()
    saving=page.get_by_role('dialog',name='Save pivot',exact=True);p.keyboard_dialog(page,saving)
    saving.get_by_label('Name',exact=True).fill(PIVOT)
    with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+'/views')as made:saving.get_by_role('button',name='Save view',exact=True).click()
    created=made.value.json();assert made.value.ok and created['kind']=='pivot'and created['revision']==1,created
    expect(saving).not_to_be_visible()
    saved=data.get_by_role('region',name='Pivot '+PIVOT,exact=True);expect(saved).to_be_visible()
    expect(saved.get_by_role('button',name='Save changes',exact=True)).to_have_count(0)
    expect(side(page).get_by_role('navigation',name='Pivots',exact=True).get_by_role('button',name=PIVOT,exact=True)).to_be_visible();hide_side(page)
    grid=saved.get_by_role('grid',name=PIVOT,exact=True);expect(grid.locator('tbody tr')).to_have_count(2)
    assert [r[0]for r in pivot_rows(grid)]==['East','West']
    capture(page,out,report,'pivot-saved-view',theme,width)
    # A saved, unchanged pivot drills through the server, and its runs are attributed to the view.
    with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+f'/views/{created["id"]}/drill')as drill:
        grid.locator('tbody tr').nth(1).get_by_role('gridcell').first.click()
    answer=drill.value.json();assert drill.value.ok and answer['exact']is True,answer
    assert any(item.get('value')=='West'for item in answer['spec']['filters']['items']),answer
    expect(data.get_by_role('region',name=f'Table West · {MONTH}',exact=True)).to_be_visible()
    runs=request(api,'GET',prefix+f'/views/{created["id"]}/runs')['runs'];assert runs and all(r['view_revision']==1 for r in runs),runs
    report['checks'].append({'check':'pivot-build-drill-save-run-history','theme':theme,'width':width,'pivot':expected,'drill_rows_east':east_rows,'server_drill_exact':answer['exact'],'view_runs':len(runs)})
    return created

def segment_journey(page,data,api,path,prefix,cid,out,report,theme,width):
    """Create a segment from table filters, reuse it as a filter in another table tab, edit it and read the audit."""
    # Start from a clean table: the earlier journey left combined tables and Show SQL open in the sales tab, so close it and open it again.
    side(page).get_by_role('navigation',name='Tables',exact=True).get_by_role('button',name='sales',exact=True).click()
    data.get_by_role('button',name='Close sales',exact=True).click();hide_side(page)
    side(page).get_by_role('navigation',name='Tables',exact=True).get_by_role('button',name='sales',exact=True).click()
    tab=data.get_by_role('region',name='Table sales',exact=True);expect(tab.get_by_text('Rows 1 to 200',exact=True)).to_be_visible()
    expect(tab.get_by_role('list',name='Combined tables',exact=True)).to_have_count(0)
    add_amount_filter(page,tab,'gt','2400')
    expect(tab.get_by_role('button',name='Amount is greater than 2400',exact=True)).to_be_visible()
    expect(tab.get_by_text('Rows 1 to 101 of 101',exact=True)).to_be_visible();expect(tab.locator('[data-stale]')).to_have_count(0)
    tab.get_by_role('button',name='Save as segment',exact=True).click()
    naming=page.get_by_role('dialog',name='Save as segment',exact=True);p.keyboard_dialog(page,naming)
    capture(page,out,report,'segment-save-dialog',theme,width)
    naming.get_by_label('Name',exact=True).fill(SEGMENT)
    with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+'/views')as made:naming.get_by_role('button',name='Save segment',exact=True).click()
    segment=made.value.json();assert made.value.ok and segment['kind']=='segment'and segment['revision']==1,segment
    expect(naming).not_to_be_visible();expect(tab.get_by_text('Saved segment '+SEGMENT,exact=True)).to_be_visible()
    expect(segment_button(page)).to_be_visible();hide_side(page)
    # Reuse it in another table tab: the East rows of the month that the pivot drill opened.
    tab_named(data,f'East · {MONTH}').click()
    other=data.get_by_role('region',name=f'Table East · {MONTH}',exact=True);expect(other).to_be_visible()
    other.get_by_role('button',name='Add segment',exact=True).click()
    menu=page.get_by_role('dialog',name='Add segment filter',exact=True)
    menu.get_by_role('button',name=SEGMENT,exact=True).click()
    expect(other.get_by_role('button',name=re.compile('^In segment '+SEGMENT))).to_be_visible()
    members=oracle_count(path,"region='East' AND day>=? AND day<? AND amount>2400",(MONTH+'-01','2026-02-01'))
    expect(other.get_by_text(f'Rows 1 to {members} of {members}',exact=True)).to_be_visible()
    capture(page,out,report,'segment-used-in-table-tab',theme,width)
    # The segment tab: plain definition, one primary action, audit with the creation revision.
    segment_button(page).click();hide_side(page)
    seg=data.get_by_role('region',name='Segment '+SEGMENT,exact=True);expect(seg).to_be_visible()
    expect(seg.get_by_text('Amount is greater than 2400')).to_be_visible()
    expect(seg.get_by_role('button',name='Show underlying rows',exact=True)).to_be_visible()
    audit=seg.get_by_role('region',name='Audit',exact=True);expect(audit.get_by_role('list',name='Revisions',exact=True)).to_contain_text('Created by '+p.USERNAME)
    capture(page,out,report,'segment-tab-audit-created',theme,width)
    # Edit: replace the filter, save, and see revision 2 with who and what changed.
    seg.get_by_role('button',name='Edit filters',exact=True).click()
    add_amount_filter(page,seg,'gt','2450')
    seg.get_by_role('button',name='Remove filter: Amount is greater than 2400',exact=True).click()
    expect(seg.get_by_role('button',name='Save changes',exact=True)).to_be_visible()
    with page.expect_response(lambda r:r.request.method=='PATCH'and urlsplit(r.url).path==prefix+f'/views/{segment["id"]}')as patched:seg.get_by_role('button',name='Save changes',exact=True).click()
    changed=patched.value.json();assert patched.value.ok and changed['revision']==2 and patched.value.request.post_data_json['revision']==1,changed
    revisions=audit.get_by_role('list',name='Revisions',exact=True)
    expect(revisions.locator(':scope > li')).to_have_count(2)
    expect(revisions.locator(':scope > li').first).to_contain_text('Revision 2 · '+p.USERNAME)
    expect(revisions.locator(':scope > li').first).to_contain_text('Added filter: Amount is greater than 2450')
    expect(revisions.locator(':scope > li').first).to_contain_text('Removed filter: Amount is greater than 2400')
    history=request(api,'GET',prefix+f'/views/{segment["id"]}/history')['revisions'];assert [r['revision']for r in history]==[2,1] and history[0]['actor']['name']==p.USERNAME,history
    # Counting members is a run; it appears under Runs with who, rows, result and the revision it ran on.
    seg.get_by_role('button',name='Done editing',exact=True).click()
    seg.get_by_role('button',name='Count members',exact=True).click()
    big=oracle_count(path,'amount>2450');expect(seg.get_by_text(f'{big} members',exact=True)).to_be_visible()
    audit.get_by_role('tab',name='Runs',exact=True).click()
    runs=audit.get_by_role('table',name='Runs',exact=True);expect(runs.get_by_role('row')).to_have_count(2)
    latest=runs.get_by_role('row').nth(1);expect(latest).to_contain_text('You');expect(latest).to_contain_text('Count');expect(latest).to_contain_text('Completed')
    runs_api=request(api,'GET',prefix+f'/views/{segment["id"]}/runs')['runs'];assert runs_api and runs_api[0]['mode']=='count'and runs_api[0]['view_revision']==2 and runs_api[0]['status']=='completed',runs_api
    capture(page,out,report,'segment-audit-revision-and-runs',theme,width)
    # A segment that a saved view uses cannot be deleted; the server says who uses it.
    pivot=views_api(api,prefix)[PIVOT];detail=request(api,'GET',prefix+'/views/'+pivot['id'])
    using=json.loads(json.dumps(detail['spec']));using['filters']['items'].append({'segment':segment['id'],'negate':False})
    holder=request(api,'POST',prefix+'/views',{'kind':'pivot','name':'Big sales by region','spec':using},code=201)
    current=request(api,'GET',prefix+'/views/'+segment['id'])
    blocked=api.request('DELETE',prefix+f'/views/{segment["id"]}?revision={current["revision"]}')
    assert blocked.status_code==409 and blocked.json()['detail']['code']=='segment_in_use'and blocked.json()['detail']['used_by'][0]['name']=='Big sales by region',blocked.text
    request(api,'DELETE',prefix+f'/views/{holder["id"]}?revision={holder["revision"]}')
    report['checks'].append({'check':'segment-create-reuse-edit-audit-runs','theme':theme,'width':width,'members_in_east_month':members,'members_after_edit':big,'revisions':[r['revision']for r in history],'delete_in_use_409':True})
    return segment,big

def viewer_views_checks(viewer_page,member_api,prefix,segment,pivot,big,out,report,theme,width):
    """A viewer reads and counts a segment but cannot edit, see history, drill or save."""
    listed=views_api(member_api,prefix);assert {SEGMENT,PIVOT}<=set(listed),listed
    seen=request(member_api,'GET',prefix+'/views/'+segment['id']);assert seen['can_edit']is False and seen['can_run']is True and seen['used_by']==[],seen
    denials={'create':denied(member_api,'POST',prefix+'/views',{'kind':'segment','name':'Denied viewer segment','spec':seen['spec']},codes=(403,)),
        'update':denied(member_api,'PATCH',prefix+'/views/'+segment['id'],{'revision':seen['revision'],'name':'Denied rename'},codes=(403,)),
        'delete':denied(member_api,'DELETE',prefix+f'/views/{segment["id"]}?revision={seen["revision"]}',codes=(403,)),
        'history':denied(member_api,'GET',prefix+f'/views/{segment["id"]}/history',codes=(403,)),
        'drill':denied(member_api,'POST',prefix+f'/views/{pivot["id"]}/drill',{'coordinates':{'rows':['East'],'column':{'value':MONTH,'empty':False}}},codes=(403,)),
        'segment_drill':denied(member_api,'POST',prefix+f'/views/{segment["id"]}/drill',{'coordinates':{}},codes=(403,))}
    segment_button(viewer_page).click();hide_side(viewer_page)
    data=viewer_page.get_by_role('region',name='Data Studio',exact=True)
    seg=data.get_by_role('region',name='Segment '+SEGMENT,exact=True);expect(seg).to_be_visible()
    for name in('Edit filters','Show underlying rows','Save changes','Use as source for a pivot'):expect(seg.get_by_role('button',name=name,exact=True)).to_have_count(0)
    expect(seg.get_by_role('button',name='More actions for this segment',exact=True)).to_have_count(0)
    audit=seg.get_by_role('region',name='Audit',exact=True);expect(audit.get_by_role('tab',name='Revisions',exact=True)).to_have_count(0)
    expect(audit.get_by_role('tab',name='Your runs',exact=True)).to_be_visible()
    seg.get_by_role('button',name='Count members',exact=True).click()
    expect(seg.get_by_text(f'{big} members',exact=True)).to_be_visible()
    runs=audit.get_by_role('table',name='Your runs',exact=True);expect(runs.get_by_role('row')).to_have_count(2)
    expect(runs.get_by_role('row').nth(1).get_by_role('rowheader')).to_have_text('You')
    mine=request(member_api,'GET',prefix+f'/views/{segment["id"]}/runs')['runs'];assert mine and all(r['mine']for r in mine),mine
    capture(viewer_page,out,report,'viewer-segment-read-only',theme,width)
    report['checks'].append({'check':'viewer-views-read-only','theme':theme,'width':width,'denials':denials,'own_runs':len(mine)})

def conflict_journey(page,data,api,member_api,prefix,segment,out,report,theme,width):
    """The owner holds a stale revision while an editor saves; the owner's save is refused with a banner and nothing is overwritten."""
    segment_button(page).click();hide_side(page)
    seg=data.get_by_role('region',name='Segment '+SEGMENT,exact=True);expect(seg).to_be_visible()
    held=request(api,'GET',prefix+'/views/'+segment['id'])['revision']
    seg.get_by_role('button',name='Edit filters',exact=True).click()
    add_amount_filter(page,seg,'lt','2490')
    expect(seg.get_by_role('button',name='Save changes',exact=True)).to_be_visible()
    theirs=request(member_api,'PATCH',prefix+'/views/'+segment['id'],{'revision':held,'description':'Edited by the second editor'})
    assert theirs['revision']==held+1 and theirs['updated_by']['name']==SECOND,theirs
    with page.expect_response(lambda r:r.request.method=='PATCH'and urlsplit(r.url).path==prefix+f'/views/{segment["id"]}')as stale:seg.get_by_role('button',name='Save changes',exact=True).click()
    assert stale.value.status==409,stale.value.text()
    detail=stale.value.json()['detail'];assert detail['code']=='revision_conflict'and detail['current_revision']==held+1 and detail['updated_by']['name']==SECOND,detail
    banner=seg.get_by_role('alert');expect(banner.get_by_role('heading')).to_contain_text(SECOND+' changed this view')
    expect(banner.get_by_role('heading')).to_be_focused()
    capture(page,out,report,'segment-conflict-banner',theme,width,expect_alert=True)
    assert request(api,'GET',prefix+'/views/'+segment['id'])['revision']==held+1,'The stale save changed the view'
    banner.get_by_role('button',name='Reload latest',exact=True).click()
    expect(seg.get_by_text(f'Segment on Sales · revision {held+1}',exact=True)).to_be_visible()
    expect(seg.get_by_role('alert')).to_have_count(0)
    revisions=seg.get_by_role('region',name='Audit',exact=True).get_by_role('list',name='Revisions',exact=True)
    expect(revisions.locator(':scope > li').first).to_contain_text(f'Revision {held+1} · {SECOND}')
    final=request(api,'GET',prefix+'/views/'+segment['id']);assert final['revision']==held+1 and len(final['spec']['filters']['items'])==1,final
    report['checks'].append({'check':'segment-409-conflict-banner-reload','theme':theme,'width':width,'held_revision':held,'current_revision':held+1,'editor':SECOND,'stale_save_persisted':False})



def finish(api,wid,job,page=None):
    value=job
    def done():
        nonlocal value
        value=request(api,'GET',f'/api/data/workspaces/{wid}/jobs/{job["id"]}')
        return value['status']not in('queued','running')
    p.wait(done,page=page,seconds=30)
    assert value['status']=='completed',value
    return value

def browser_run(page,api,wid,button,pattern):
    with page.expect_response(lambda r:r.request.method=='POST'and re.fullmatch(pattern,urlsplit(r.url).path))as result:button.click()
    response=result.value;assert response.ok,response.text()
    value=finish(api,wid,response.json(),page)
    value['_qa_request']=response.request.post_data_json
    return value

def dialog_check(page,dialog,report,label,field=None):
    p.keyboard_dialog(page,dialog)
    if field:focus_sample(page,dialog.get_by_role('textbox',name=field,exact=True),label,report,True)

def business_file():
    folder=Path(tempfile.mkdtemp(prefix='termx-data-business-qa-',dir='/tmp'))
    path=folder/'Synthetic-QA-business.sqlite3'
    with sqlite3.connect(path)as db:
        db.execute('CREATE TABLE regions(id INTEGER PRIMARY KEY, name TEXT NOT NULL)')
        db.executemany('INSERT INTO regions VALUES(?,?)',[(1,'North'),(2,'South'),(3,'East'),(4,'West')])
        db.execute('CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, region_id INTEGER NOT NULL REFERENCES regions(id))')
        db.executemany('INSERT INTO customers VALUES(?,?,?)',[(n,'Customer '+str(n).zfill(2),1+(n%4))for n in range(1,51)])
        db.execute('CREATE TABLE sales(id INTEGER PRIMARY KEY, region TEXT NOT NULL, amount INTEGER NOT NULL, day DATE NOT NULL, customer_id INTEGER NOT NULL REFERENCES customers(id))')
        db.executemany('INSERT INTO sales VALUES(?,?,?,?,?)',[(n,'East'if n%2 else'West',n,'2026-01-'+str(1+(n%28)).zfill(2),1+(n%50))for n in range(1,ROWS+1)])
    return folder,path.resolve()

def pick_business_file(page,context,base,path,out,report,theme,width):
    dialog=page.get_by_role('dialog',name='Connect database',exact=True)
    dialog.get_by_role('button',name='Browse files',exact=True).click()
    picker=page.get_by_role('dialog',name='Choose file',exact=True);p.keyboard_dialog(page,picker)
    initial=context.request.get(base+'/api/workspace/paths?kind=file').json()
    current=Path(initial['path']);target=path.parent
    common=Path(os.path.commonpath([current,target]))
    while current!=common:
        picker.get_by_role('button',name='Parent folder',exact=True).click();current=current.parent
        expect(picker.locator('.host-path-current')).to_have_attribute('title',str(current))
    for part in target.relative_to(common).parts:
        picker.get_by_role('textbox',name='Filter files',exact=True).fill(part)
        picker.get_by_role('button',name='Open folder '+part,exact=True).click();current=current/part
        expect(picker.locator('.host-path-current')).to_have_attribute('title',str(current))
    picker.get_by_role('button',name='Select file '+path.name,exact=True).click()
    capture(page,out,report,'sqlite-host-file-chooser',theme,width)
    picker.get_by_role('button',name='Choose file',exact=True).click();expect(picker).not_to_be_visible()
    expect(dialog.locator('.host-path-choice')).to_contain_text(str(path))

def copilot_transport(state,report):
    from termx.agent.providers import OpenAIResponsesAdapter
    clients=[];fixture={'wid':'','cid':'','qid':'','round':0}
    state.agent.save_provider(provider_id='synthetic-data-copilot',kind='openai-compatible',name='Synthetic Data Copilot',base_url='https://inference.invalid/v1',model='synthetic-data-model',capabilities=['functions'],api_key=KEY,wire='responses')
    def handler(req):
        body=json.loads(req.content);fixture['round']+=1;n=fixture['round']
        # Authorization belongs only to the transport header; source paths,
        # credentials and host configuration never belong to the LLM payload.
        encoded=json.dumps(body)
        assert KEY not in encoded,'Fixture credential entered inference body'
        assert '/Users/'not in encoded,'Host path entered inference body'
        assert 'Synthetic-QA-business.sqlite3'not in encoded,'Source path entered inference body'
        assert req.headers.get('authorization')=='Bearer '+KEY,'Fixture provider auth header mismatch'
        names={t.get('name',t.get('function',{}).get('name'))for t in body.get('tools',[])}
        assert not(names&{'run_shell','computer','write_file'}),'Native tools entered Data model payload'
        report['inference_mock_requests'].append({'round':n,'path':req.url.path,'tool_names':sorted(names),'payload_bytes':len(req.content),'external':False})
        tools={1:('read_schema',{'connection_id':fixture['cid']}),2:('read_query',{'connection_id':fixture['cid'],'sql':'SELECT COUNT(*) AS rows, SUM(amount) AS total FROM sales'}),3:('propose_query',{'name':'Synthetic Copilot total','connection_id':fixture['cid'],'sql':'SELECT COUNT(*) AS rows, SUM(amount) AS total FROM sales'}),4:('propose_report',{'name':'Synthetic Copilot report proposal','query_id':fixture['qid'],'visual':{'kind':'metric','y':'amount','aggregate':'sum'},'filters':[]})}
        if n in tools:
            name,args=tools[n]
            output=[{'type':'function_call','call_id':'synthetic-data-tool-'+str(n),'name':name,'arguments':json.dumps(args)}]
        else:
            assert str(TOTAL)in encoded and str(ROWS)in encoded,'Actual SQL tool results did not reach the model loop'
            output=[{'type':'message','role':'assistant','content':[{'type':'output_text','text':f'Synthetic QA observation: {ROWS} rows total {TOTAL}. The proposals are drafts for review.'}]}]
        return httpx.Response(200,json={'id':'synthetic-data-response-'+str(n),'status':'completed','output':output})
    def adapter(provider):
        assert provider['id']=='synthetic-data-copilot'
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler),headers={'Authorization':'Bearer '+KEY});clients.append(client)
        instance=OpenAIResponsesAdapter(base_url=provider['base_url'],model=provider['model'],api_key=KEY,capabilities=provider['capabilities'],client=client)
        original=instance.turn
        async def observed(**kwargs):
            try:return await original(**kwargs)
            except BaseException as error:
                report.setdefault('mock_adapter_errors',[]).append({'type':type(error).__name__,'detail':str(error).replace(KEY,'[fixture key]')})
                raise
        instance.turn=observed
        return instance
    state.agent._adapter=adapter
    return fixture,clients

def copilot_browser(page,api,wid,cid,qid,mock,out,report,theme,width):
    prefix=f'/api/data/workspaces/{wid}'
    assert mock['round']==0,'Data initialization silently triggered model inference'
    data=page.get_by_role('region',name='Data Studio',exact=True)
    side(page).get_by_role('button',name='Synthetic full sales',exact=True).click()
    editor_panel=page.get_by_role('dialog',name='SQL editor',exact=True)
    if editor_panel.count():close_advanced(page,editor_panel)
    data.get_by_role('button',name='Copilot',exact=True).click()
    panel=page.get_by_role('region',name='Data Copilot chat',exact=True)
    expect(panel.get_by_role('heading',name='Connect Copilot',exact=True)).to_be_visible()
    expect(panel.get_by_role('textbox',name='Message Copilot',exact=True)).to_be_disabled()
    capture(page,out,report,'copilot-unconfigured',theme,width)
    panel.get_by_role('button',name='Set up Copilot',exact=True).click()
    setup=page.get_by_role('dialog',name='Copilot settings',exact=True);p.keyboard_dialog(page,setup)
    setup.get_by_role('combobox',name='Account',exact=True).select_option('synthetic-data-copilot')
    model=setup.get_by_role('textbox',name='Model',exact=True)
    if model.count():model.fill('synthetic-data-model')
    else:setup.get_by_role('combobox',name='Model',exact=True).select_option('synthetic-data-model')
    expect(setup.get_by_role('button',name='Off',exact=True)).to_have_attribute('aria-pressed','false')
    capture(page,out,report,'copilot-explicit-account-setup',theme,width)
    setup.get_by_role('button',name='Save',exact=True).click();expect(setup).not_to_be_visible()
    schema=panel.get_by_role('button',name='Schema',exact=True);results=panel.get_by_role('button',name='Results',exact=True)
    expect(schema).to_be_enabled();expect(results).to_be_enabled()
    expect(schema).to_have_attribute('aria-pressed','false');expect(results).to_have_attribute('aria-pressed','false')
    expect(panel.get_by_role('checkbox')).to_have_count(0)
    assert mock['round']==0,'Copilot setup silently triggered inference'
    schema.click();results.click()
    message=panel.get_by_role('textbox',name='Message Copilot',exact=True)
    message.fill('Synthetic QA: inspect the selected schema, calculate the full sales total and propose a query and report. Do not publish anything.')
    focus_sample(page,message,'Copilot message',report,True)
    before_queries=len(request(api,'GET',prefix+'/queries'));before_reports=len(request(api,'GET',prefix+'/reports'))
    with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+'/copilot/messages')as sent:panel.get_by_role('button',name='Send message',exact=True).click()
    response=sent.value;assert response.ok,response.text();run=response.json();body=response.request.post_data_json
    assert body['connection_id']==cid and body['query_id']==qid and body['include_schema']and body['include_results']
    final=None
    def done():
        nonlocal final
        final=request(api,'GET',prefix+'/copilot/runs/'+run['id']);return final['status']not in('queued','running')
    p.wait(done,page=page,seconds=30)
    assert final['status']=='completed'and str(TOTAL)in final['answer']and len(final['proposals'])==2,final
    assert mock['round']==5,mock
    replay=request(api,'POST',prefix+'/copilot/messages',body)
    assert replay['id']==run['id']and mock['round']==5,'Copilot replay repeated inference'
    assert len(request(api,'GET',prefix+'/queries'))==before_queries and len(request(api,'GET',prefix+'/reports'))==before_reports,'Proposal silently persisted a query/report'
    expect(panel.locator('.copilot-answer')).to_contain_text(str(TOTAL))
    activity=panel.get_by_role('button',name=re.compile('^View activity'))
    expect(activity).to_have_attribute('data-state','closed')
    capture(page,out,report,'copilot-actual-sql-tool-proposals',theme,width)
    report_proposal=panel.locator('.copilot-proposal').filter(has_text='Synthetic Copilot report proposal')
    with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+'/reports')as saved:report_proposal.get_by_role('button',name='Save report',exact=True).click()
    assert saved.value.ok,saved.value.text()
    assert len(request(api,'GET',prefix+'/reports'))==before_reports+1
    query_proposal=panel.locator('.copilot-proposal').filter(has_text='Synthetic Copilot total')
    query_proposal.get_by_role('button',name='Open query',exact=True).click()
    expect(page.locator('#data-sql')).to_contain_text('SELECT COUNT(*) AS rows, SUM(amount) AS total FROM sales')
    panel.get_by_role('button',name='Close Copilot',exact=True).click();expect(panel).to_have_count(0)
    expect(page.locator('#data-sql')).to_be_visible()
    capture(page,out,report,'copilot-open-query-draft',theme,width)
    reduced=data.locator('*').evaluate_all("rows=>rows.filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>({animation:getComputedStyle(e).animationName,transition:getComputedStyle(e).transitionDuration})).filter(s=>s.animation!=='none'||s.transition.split(',').some(v=>parseFloat(v)>0))")
    assert not reduced,('Data motion remained active under reduce',reduced)
    report['checks'].append({'check':'real-adapter-mock-inference-actual-sqlite-tools-through-ui','theme':theme,'width':width,'inference_requests':5,'tools':[e['title']for e in final['events']if e['kind']=='tool.completed'],'total_observed':TOTAL,'typed_proposals':2,'proposal_auto_saves':0,'explicit_report_save':True,'explicit_query_draft_open':True,'replayed_run_no_inference':True,'context_opted_in':{'schema':True,'results':True},'team_account_share':False,'visible_motion_under_reduce':reduced})


def policy_checks(page,browser,base,api,state,owner,wid,cid,qid,rid,did,ownerjob,views_state,out,report,theme,width):
    prefix=f'/api/data/workspaces/{wid}'
    editor_panel=page.get_by_role('dialog',name='SQL editor',exact=True)
    if editor_panel.count():close_advanced(page,editor_panel)
    nav(page,'Team');team=page.get_by_role('region',name='Workspace team',exact=True)
    team.get_by_role('button',name='Add member',exact=True).click()
    dialog=page.get_by_role('dialog',name='Add workspace member',exact=True);dialog_check(page,dialog,report,'Exact team username','Exact username')
    dialog.get_by_role('textbox',name='Exact username',exact=True).fill(SECOND)
    dialog.get_by_role('combobox',name='Workspace role',exact=True).select_option('viewer')
    dialog.get_by_role('button',name='Add member',exact=True).click();expect(dialog).not_to_be_visible()
    expect(team).to_contain_text(SECOND)
    capture(page,out,report,'team-explicit-viewer',theme,width)
    member_api=login_api(base,SECOND)
    try:
        viewer=request(member_api,'GET',prefix);assert viewer['workspace']['role']=='viewer'and not viewer['connections']and not viewer['queries']
        checks={'viewer_arbitrary_sql':denied(member_api,'POST',prefix+'/query-runs',{'request_id':'viewer-denied','connection_id':cid,'sql':'SELECT * FROM sales'}),'viewer_draft_create':denied(member_api,'POST',prefix+'/reports',{'name':'Denied viewer draft','query_id':qid,'visual':{'kind':'table','aggregate':'none'}}),'viewer_unshared_report_run':denied(member_api,'POST',prefix+f'/reports/{rid}/run',{'request_id':'unshared-denied'}),'foreign_job':denied(member_api,'GET',prefix+f'/jobs/{ownerjob["id"]}')}
        source=request(api,'GET',prefix+'/connections')[0]
        source=request(api,'PATCH',prefix+'/connections/'+cid,{'revision':source['revision'],'shared_read':True})
        draft=request(api,'POST',prefix+'/reports',{'name':'Synthetic unpublished report','query_id':qid,'visual':{'kind':'table','aggregate':'none'},'filters':[]})
        checks['viewer_unpublished_draft']=denied(member_api,'GET',prefix+'/reports/'+draft['id'])
        visible=request(member_api,'GET',prefix+'/reports/'+rid)
        assert visible['name']=='Synthetic regional revenue'
        vjob=finish(member_api,wid,request(member_api,'POST',prefix+f'/reports/{rid}/run',{'request_id':'viewer-published'}))
        assert dict(vjob['result']['rows'])==EXPECTED and vjob['report_context']['semantics']=='full_query'
        original=request(api,'GET',prefix+'/reports/'+rid)
        request(api,'PATCH',prefix+'/reports/'+rid,{'revision':original['revision'],'name':'Synthetic unpublished title change'})
        query=request(api,'GET',prefix+'/queries')[0]
        request(api,'PATCH',prefix+'/queries/'+qid,{'revision':query['revision'],'sql':'SELECT id,region,amount,day FROM sales WHERE id < 10'})
        assert request(member_api,'GET',prefix+'/reports/'+rid)['name']=='Synthetic regional revenue'
        pinned=finish(member_api,wid,request(member_api,'POST',prefix+f'/reports/{rid}/run',{'request_id':'viewer-pinned-publication'}))
        assert dict(pinned['result']['rows'])==EXPECTED,'Published query snapshot changed with draft SQL'
        viewer_context=browser.new_context(viewport={'width':width,'height':812 if width<640 else 900},has_touch=width<640,reduced_motion='reduce')
        protect_context(viewer_context,base,report)
        viewer_context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
        viewer_page=viewer_context.new_page();viewer_page.on('pageerror',lambda err:report['errors'].append(str(err)))
        try:
            authenticate(viewer_page,base,SECOND,'/?workspace=data&data_workspace='+wid+'&data_view=bi')
            viewer_bi=viewer_page.get_by_role('region',name='BI workspace',exact=True)
            expect(viewer_bi.get_by_role('button',name='Run report',exact=True)).to_be_visible()
            expect(viewer_bi.get_by_role('button',name='Edit report',exact=True)).to_have_count(0)
            browser_run(viewer_page,member_api,wid,viewer_bi.get_by_role('button',name='Run report',exact=True),re.escape(prefix)+r'/reports/[^/]+/run')
            expect(viewer_bi.get_by_role('img',name='Synthetic regional revenue — bar chart',exact=True)).to_be_visible()
            viewer_bi.get_by_role('img',name='Synthetic regional revenue — bar chart',exact=True).scroll_into_view_if_needed()
            capture(viewer_page,out,report,'viewer-published-report',theme,width)
            viewer_views_checks(viewer_page,member_api,prefix,views_state['segment'],views_state['pivot'],views_state['big'],out,report,theme,width)
        finally:viewer_context.close()
        members=request(api,'GET',prefix+'/members');member=next(row for row in members if row['principal_id']!=owner.id)
        request(api,'PATCH',prefix+'/members/'+member['id'],{'revision':member['revision'],'role':'editor'})
        editorjob=finish(member_api,wid,request(member_api,'POST',prefix+'/query-runs',{'request_id':'editor-shared-read','connection_id':cid,'sql':'SELECT COUNT(*) AS rows FROM sales'}))
        assert editorjob['result']['rows']==[[ROWS]]
        conflict_journey(page,page.get_by_role('region',name='Data Studio',exact=True),api,member_api,prefix,views_state['segment'],out,report,theme,width)
        checks['editor_modify_other_source']=denied(member_api,'PATCH',prefix+'/connections/'+cid,{'revision':source['revision'],'name':'Denied source mutation'})
        checks['copilot_personal_account_not_shared']=denied(member_api,'POST',prefix+'/copilot/messages',{'request_id':'member-personal-denied','message':'Read this source'})
        source=request(api,'PATCH',prefix+'/connections/'+cid,{'revision':source['revision'],'shared_read':False})
        checks['source_grant_revoked_query']=denied(member_api,'POST',prefix+'/query-runs',{'request_id':'editor-revoked','connection_id':cid,'sql':'SELECT COUNT(*) FROM sales'})
        checks['source_grant_revoked_job']=denied(member_api,'GET',prefix+'/jobs/'+editorjob['id'])
        checks['source_grant_revoked_export']=denied(member_api,'GET',prefix+'/jobs/'+editorjob['id']+'/export.csv')
        member=next(row for row in request(api,'GET',prefix+'/members')if row['principal_id']!=owner.id)
        request(api,'DELETE',prefix+'/members/'+member['id'],{'revision':member['revision']})
        checks['membership_revoked_snapshot']=denied(member_api,'GET',prefix)
        checks['membership_revoked_report']=denied(member_api,'GET',prefix+'/reports/'+rid)
        report['checks'].append({'check':'actual-managed-second-user-live-workspace-source-and-publication-policy','theme':theme,'width':width,'denials':checks,'viewer_full_total':TOTAL,'published_query_snapshot_pinned':True,'editor_shared_query_rows':ROWS})
    finally:member_api.close()


def exercise(browser,out,report,ui_dist,width,theme):
    folder,path=business_file();before=hashlib.sha256(path.read_bytes()).hexdigest()
    with p.isolated_host(ui_dist,report)as(state,owner,agent_project,base,scratch):
        from termx.authorization import SCOPES
        state.identity.create_local_user(SECOND,p.PASSWORD,list(SCOPES))
        mock,clients=copilot_transport(state,report)
        context=browser.new_context(viewport={'width':width,'height':812 if width<640 else 900},has_touch=width<640,reduced_motion='reduce',accept_downloads=True)
        context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
        protect_context(context,base,report);page=context.new_page();page.on('pageerror',lambda err:report['errors'].append(str(err)))
        api=login_api(base)
        try:
            assert request(api,'GET','/api/data/bootstrap')['workspaces']==[]
            assert not report['inference_mock_requests'] or mock['round']==0
            authenticate(page,base)
            data=page.get_by_role('region',name='Data Studio',exact=True)
            expect(data.get_by_role('heading',name='Your data, together',exact=True)).to_be_visible()
            capture(page,out,report,'data-empty-first-launch',theme,width)
            data.get_by_role('button',name='Create workspace',exact=True).click()
            dialog=page.get_by_role('dialog',name='Create workspace',exact=True);dialog_check(page,dialog,report,'New team workspace','Workspace name')
            dialog.get_by_role('textbox',name='Workspace name',exact=True).fill('Synthetic QA team')
            page.keyboard.press('Escape');expect(dialog).not_to_be_visible()
            data.get_by_role('button',name='Create workspace',exact=True).click()
            dialog.get_by_role('textbox',name='Workspace name',exact=True).fill('Synthetic QA team')
            with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path=='/api/data/workspaces')as created:dialog.get_by_role('button',name='Create workspace',exact=True).click()
            workspace=created.value.json();wid=workspace['id'];mock['wid']=wid;prefix=f'/api/data/workspaces/{wid}'
            expect(dialog).not_to_be_visible()
            workspace_select=side(page).get_by_role('combobox',name='Data workspace',exact=True)
            expect(workspace_select).to_be_enabled();expect(workspace_select).to_have_value(wid)
            focus_sample(page,workspace_select,'Data workspace selector',report,True);hide_side(page)
            data.get_by_role('button',name='Connect database',exact=True).last.click()
            connection_dialog=page.get_by_role('dialog',name='Connect database',exact=True)
            dialog_check(page,connection_dialog,report,'Business source name','Connection name')
            connection_dialog.get_by_role('textbox',name='Connection name',exact=True).fill('Synthetic QA sales')
            connection_dialog.get_by_role('combobox',name='Database engine',exact=True).select_option('sqlite')
            expect(connection_dialog.get_by_role('checkbox',name='Allow team members to read this source',exact=True)).not_to_be_checked()
            pick_business_file(page,context,base,path,out,report,theme,width)
            with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+'/connections')as connected:connection_dialog.get_by_role('button',name='Save connection',exact=True).click()
            connection=connected.value.json();assert connected.value.ok,connection;cid=connection['id'];mock['cid']=cid
            expect(connection_dialog).not_to_be_visible()
            assert connection['config']['path']==str(path)and not connection['shared_read']
            with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+f'/connections/{cid}/test')as tested:data.get_by_role('button',name='Test connection',exact=True).click()
            assert tested.value.json()['ok']
            data.get_by_role('button',name='Load schema',exact=True).first.click()
            overview=data.get_by_role('region',name='Tables overview',exact=True)
            for table in('customers','regions','sales'):expect(overview).to_contain_text(table)
            capture(page,out,report,'tables-overview',theme,width)
            overview.get_by_role('button',name='Count rows in sales',exact=True).click()
            expect(overview.get_by_text(f'{ROWS:,}',exact=True)).to_be_visible()
            visual_journey(page,data,path,out,report,theme,width)
            pivot=pivot_journey(page,data,api,path,prefix,cid,out,report,theme,width)
            segment,big=segment_journey(page,data,api,path,prefix,cid,out,report,theme,width)
            views_state={'pivot':pivot,'segment':segment,'big':big}
            panel=open_advanced(page)
            sql=set_sql(page,SQL);focus_sample(page,page.locator('#data-sql'),'Read-only SQL editor',report,True)
            capture(page,out,report,'sqlite-advanced-panel',theme,width)
            job=browser_run(page,api,wid,panel.get_by_role('button',name='Run',exact=True),re.escape(prefix)+'/query-runs')
            assert job['result']['truncated']and len(job['result']['rows'])==1000
            replay=request(api,'POST',prefix+'/query-runs',job['_qa_request'])
            assert replay['id']==job['id'],'Same request ID did not return the original job'
            finish(api,wid,replay,page)
            close_advanced(page,panel)
            results=data.get_by_role('region',name='Query results',exact=True)
            expect(results).to_contain_text('Preview truncated.');expect(results.get_by_role('table',name='Result table',exact=True)).to_be_visible()
            results.scroll_into_view_if_needed()
            capture(page,out,report,'readonly-preview-truncated',theme,width)
            with page.expect_download()as downloading:data.get_by_role('button',name='Export CSV',exact=True).click()
            exported=Path(downloading.value.path()).read_text();rows=list(csv.reader(io.StringIO(exported)));assert len(rows)==1001 and rows[0]==['id','region','amount','day']
            panel=open_advanced(page)
            panel.get_by_role('button',name='Save query',exact=True).click()
            saving=page.get_by_role('dialog',name='Save query',exact=True);saving.get_by_role('textbox',name='Query name',exact=True).fill('Synthetic full sales')
            with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+'/queries')as saved:saving.get_by_role('button',name='Save query',exact=True).click()
            query=saved.value.json();qid=query['id'];mock['qid']=qid;expect(saving).not_to_be_visible()
            set_sql(page,LONG_SQL)
            with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+'/query-runs')as longrun:panel.get_by_role('button',name='Run',exact=True).click()
            longjob=longrun.value.json();panel.get_by_role('button',name='Cancel',exact=True).click()
            p.wait(lambda:request(api,'GET',prefix+'/jobs/'+longjob['id'])['status']=='cancelled',page=page,seconds=30)
            close_advanced(page,panel)
            expect(data.get_by_role('region',name='Query results',exact=True)).to_contain_text('Cancelled')
            data.get_by_role('region',name='Query results',exact=True).scroll_into_view_if_needed()
            capture(page,out,report,'sqlite-query-cancelled',theme,width)
            denied(api,'POST',prefix+'/query-runs',{'request_id':'mutation-denied','connection_id':cid,'sql':'DELETE FROM sales'},codes=(400,422,403))
            nav(page,'BI');bi=page.get_by_role('region',name='BI workspace',exact=True)
            bi.get_by_role('button',name='New report',exact=True).click()
            creating=page.get_by_role('dialog',name='Create report',exact=True);dialog_check(page,creating,report,'New report name','Name')
            creating.get_by_role('textbox',name='Name',exact=True).fill('Synthetic regional revenue')
            creating.get_by_role('combobox',name='Saved query',exact=True).select_option(qid)
            with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+'/reports')as made:creating.get_by_role('button',name='Create',exact=True).click()
            rid=made.value.json()['id'];expect(creating).not_to_be_visible()
            tablejob=browser_run(page,api,wid,bi.get_by_role('button',name='Run report',exact=True),re.escape(prefix)+r'/reports/[^/]+/run')
            assert tablejob['result']['truncated']and len(tablejob['result']['rows'])==1000
            bi.get_by_role('combobox',name='Visual',exact=True).select_option('bar')
            bi.get_by_role('combobox',name='Category',exact=True).select_option('region')
            bi.get_by_role('combobox',name='Value',exact=True).select_option('amount')
            bi.get_by_role('combobox',name='Aggregate',exact=True).select_option('sum')
            bi.get_by_role('button',name='Save changes',exact=True).click();expect(bi.get_by_role('button',name='Save changes',exact=True)).to_be_disabled()
            aggregate=browser_run(page,api,wid,bi.get_by_role('button',name='Run report',exact=True),re.escape(prefix)+r'/reports/[^/]+/run')
            assert dict(aggregate['result']['rows'])==EXPECTED and aggregate['report_context']['semantics']=='full_query' and not aggregate['result']['truncated'],aggregate
            expect(bi.get_by_role('img',name='Synthetic regional revenue — bar chart',exact=True)).to_be_visible()
            focus_sample(page,bi.get_by_role('combobox',name='Visual',exact=True),'Report visual',report,True)
            capture(page,out,report,'report-controls-keyboard-focus',theme,width)
            chart=bi.get_by_role('img',name='Synthetic regional revenue — bar chart',exact=True)
            chart.scroll_into_view_if_needed();chart_geometry(chart,page,report,theme,width)
            capture(page,out,report,'full-source-aggregate-chart',theme,width)
            bi.get_by_role('button',name='View data table',exact=True).click()
            table=bi.get_by_role('region',name='Report data',exact=True)
            expect(table).to_contain_text(str(EXPECTED['East']));expect(table).to_contain_text(str(EXPECTED['West']))
            table.scroll_into_view_if_needed()
            capture(page,out,report,'full-source-aggregate-table',theme,width)
            with page.expect_download()as svg_download:bi.get_by_role('button',name='SVG',exact=True).click()
            svg=Path(svg_download.value.path()).read_text();assert '<svg'in svg and str(EXPECTED['East'])in svg.replace(',','')
            with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+f'/reports/{rid}/publish')as published:bi.get_by_role('button',name='Publish to workspace',exact=True).click()
            assert published.value.ok,published.value.text()
            bi.get_by_role('navigation',name='BI views',exact=True).get_by_role('button',name='Dashboards',exact=True).click()
            bi.get_by_role('button',name='New dashboard',exact=True).click();dashboard_dialog=page.get_by_role('dialog',name='Create dashboard',exact=True)
            dashboard_dialog.get_by_role('textbox',name='Name',exact=True).fill('Synthetic sales dashboard')
            with page.expect_response(lambda r:r.request.method=='POST'and urlsplit(r.url).path==prefix+'/dashboards')as dash:dashboard_dialog.get_by_role('button',name='Create',exact=True).click()
            did=dash.value.json()['id'];expect(dashboard_dialog).not_to_be_visible()
            bi.get_by_role('combobox',name='Report',exact=True).select_option(rid);bi.get_by_role('button',name='Add report',exact=True).click()
            bi.get_by_role('button',name='Run dashboard',exact=True).click()
            expect(bi.get_by_role('img',name='Synthetic regional revenue — bar chart',exact=True)).to_be_visible()
            bi.get_by_role('img',name='Synthetic regional revenue — bar chart',exact=True).scroll_into_view_if_needed()
            capture(page,out,report,'published-dashboard',theme,width)
            copilot_browser(page,api,wid,cid,qid,mock,out,report,theme,width)
            policy_checks(page,browser,base,api,state,owner,wid,cid,qid,rid,did,job,views_state,out,report,theme,width)
            report['checks'].append({'check':'actual-sqlite-browser-query-bi-journey','theme':theme,'width':width,'synthetic_business_rows':ROWS,'source_sha256':before,'preview_rows':1000,'preview_truncated':True,'csv_scope':'actual1000-row preview with header','full_source_aggregate':EXPECTED,'full_total':TOTAL,'readonly_delete_rejected':True,'long_recursive_query_cancelled':True,'chart_svg_downloaded':True,'published_dashboard':True,'actual_filesystem_chooser':True})
            assert hashlib.sha256(path.read_bytes()).hexdigest()==before,'Read-only source bytes changed'
            assert mock['round']==5,'Unexpected inference beyond the explicit Copilot tool loop'
        except BaseException:
            page.screenshot(path=str(out/f'failure-{theme}-{width}.png'),full_page=True)
            page.locator('input[type=password]').evaluate_all('rows=>rows.forEach(e=>e.value="")')
            (out/f'failure-{theme}-{width}.html').write_text(page.content());raise
        finally:
            api.close();context.close()
            if clients:
                async def close_all():await asyncio.gather(*(client.aclose()for client in clients))
                thread=threading.Thread(target=lambda:asyncio.run(close_all()));thread.start();thread.join(timeout=5)
                assert not thread.is_alive()
    shutil.rmtree(folder)


def run(destination,ui_dist):
    out=Path(destination).resolve();out.mkdir(parents=True,exist_ok=False)
    report={'result':'running','mode':'after','output':str(out),'ui_dist':str(Path(ui_dist).resolve()),'states':[],'checks':[],'focus':[],'backend_requests':[],'inference_mock_requests':[],'external_browser_requests':[],'errors':[],'started_at_utc':datetime.now(timezone.utc).isoformat(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'limitations':['Only explicit synthetic2501-row business SQLite files and scratch managed identities/stores are used. No real source/profile or remote database is connected.','Provider inference, when exercised, uses real adapter code over MockTransport; no paid model calls.','375px touch emulation and Chromium screenshots do not prove native/physical device behavior.','CSV exports contain the actual bounded query preview; aggregate reports execute against full source through the runtime.','Axe incomplete items remain in the report; automated checks do not establish screen-reader behavior.']}
    try:
        with sync_playwright()as playwright:
            executable=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')or next((str(path)for path in(Path(playwright.chromium.executable_path),Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))if path.is_file()),None)
            browser=playwright.chromium.launch(headless=True,executable_path=executable);report['browser']={'version':browser.version,'executable':executable}
            wanted=os.environ.get('TERMX_DATA_QA_CASES')  # optional "375:light,1280:dark" to run a subset while iterating
            for width,theme in((375,'light'),(1280,'dark'),(1280,'light'),(375,'dark')):
                if wanted and f'{width}:{theme}'not in wanted.split(','):continue
                p.phase(report,'starting-case',width=width,theme=theme);exercise(browser,out,report,ui_dist,width,theme)
            browser.close()
        assert not report['errors']and not report['external_browser_requests'],report
        assert not [s for s in report['states']if s.get('violations')],[(s['state'],s['violations'])for s in report['states']if s.get('violations')]
        report['result']='passed'
    except BaseException as error:report.update(result='failed',failure=f'{type(error).__name__}: {error}');raise
    finally:
        report['finished_at_utc']=datetime.now(timezone.utc).isoformat();assert KEY not in json.dumps(report)
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit('Usage: workspace_data_studio_e2e.py OUTPUT UI_DIST')
    r=run(sys.argv[1],sys.argv[2]);print(json.dumps({'result':r['result'],'states':len(r['states']),'checks':len(r['checks']),'mock_inference_requests':len(r['inference_mock_requests']),'ui_manifest_digest':r.get('ui_manifest_digest')},indent=2))
