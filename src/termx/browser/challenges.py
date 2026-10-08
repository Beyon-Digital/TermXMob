"""Recognizable authentication/challenge controls stay human-only.

This is a conservative host-observed boundary, not a promise to recognize
every challenge provider. It reads bounded rendered metadata, never values.
"""

_CHALLENGE = r"""selector => {
  const visible = e => { const r=e.getBoundingClientRect(),s=getComputedStyle(e); return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
  const words = /\b(?:captcha|recaptcha|hcaptcha|turnstile|mfa|one-time-code)\b|(?:verify|confirm|prove).{0,35}(?:human|identity)|(?:not|aren't|are not).{0,15}(?:robot|bot)|\bi am human\b|(?:two|multi)[ -]?factor|one[ -]?time (?:password|code)|(?:authentication|authenticator|security|verification) code/i;
  const meta = e => [e.id,e.getAttribute('name'),e.getAttribute('aria-label'),e.getAttribute('title'),e.getAttribute('autocomplete'),...(e.labels ? Array.from(e.labels).slice(0,5).map(l=>l.innerText.slice(0,500)) : []),e.innerText?.slice(0,1000)].filter(Boolean).join(' ').slice(0,3000);
  const provider = value => { try { const u=new URL(value,location.href); return ((u.hostname==='www.google.com'||u.hostname==='www.recaptcha.net'||u.hostname==='recaptcha.net') && /\/recaptcha\//.test(u.pathname)) || (/(^|\.)hcaptcha\.com$/.test(u.hostname) && /captcha/.test(u.pathname)) || (u.hostname==='challenges.cloudflare.com' && /turnstile/.test(u.pathname)); } catch { return false; } };
  if(Array.from(document.querySelectorAll('iframe')).slice(0,150).some(e=>visible(e) && (provider(e.src)||words.test(meta(e))))) return true;
  if(Array.from(document.querySelectorAll('[data-sitekey],.g-recaptcha,.h-captcha,.cf-turnstile')).slice(0,500).some(visible)) return true;
  // Do not classify an unrelated article's prose or an ordinary checkbox from
  // a word such as "challenge". Restrict textual evidence to rendered controls.
  if(Array.from(document.querySelectorAll('input,button,[role="checkbox"],[role="button"]')).slice(0,500).some(e=>visible(e) && words.test(meta(e)))) return true;
  const target=selector ? document.querySelector(selector) : null;
  return !!(target && (target.closest('[data-sitekey],.g-recaptcha,.h-captcha,.cf-turnstile') || (target.tagName==='IFRAME' && provider(target.src)) || words.test(meta(target)) || (target.closest('form,[role="dialog"]') && words.test(meta(target.closest('form,[role="dialog"]'))))));
}"""


async def requires_manual_challenge(page, selector=None):
    # Invalid selectors fail closed in the normal broker target validation.
    # A Playwright-specific selector is not a DOM selector; page-wide provider
    # checks still apply, then locator metadata covers its selected target.
    try:
        return bool(await page.evaluate(_CHALLENGE, selector))
    except Exception:
        if not selector:
            raise
        if await page.evaluate(_CHALLENGE, None):
            return True
        return bool(await page.locator(selector).first.evaluate(r"""e => !!e.closest('[data-sitekey],.g-recaptcha,.h-captcha,.cf-turnstile') || /\b(?:captcha|recaptcha|hcaptcha|turnstile|mfa|one-time-code)\b|(?:verify|confirm|prove).{0,35}(?:human|identity)|(?:not|aren't|are not).{0,15}(?:robot|bot)|\bi am human\b|(?:two|multi)[ -]?factor|one[ -]?time (?:password|code)|(?:authentication|authenticator|security|verification) code/i.test([e.id,e.name,e.getAttribute('aria-label'),e.getAttribute('autocomplete'),...(e.labels ? Array.from(e.labels).slice(0,5).map(l=>l.innerText.slice(0,500)) : []),e.innerText?.slice(0,1000)].join(' ').slice(0,3000))"""))
