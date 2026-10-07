from fastapi import Response

UI_CONTRACT = 3
HOST_CONTRACT = 3

def compatibility():
    return {'ui_contract':UI_CONTRACT,'host_contract':HOST_CONTRACT,'minimum_ui_contract':3,
            'routes':['chat','workbench','browser','computer','managers'],
            'authentication':'managed-session','client':'react-dom-workspace'}

def unavailable():
    return Response('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Update TermX Workspace</title><style>body{margin:0;background:#151716;color:#F0F3ED;font:16px system-ui;display:grid;place-items:center;min-height:100vh}main{max-width:540px;padding:32px}h1{font-size:24px}p{line-height:1.7;color:#A8B4AC}code{color:#92D4A3}</style><main><h1>Install the desktop workspace bundle</h1><p>This host requires the TermX workspace UI, contract version 3. Update the desktop app or build the workspace bundle on this host, then reload.</p><p>For a development checkout: <code>pnpm --dir desktop/workspace build</code>. Start TermX with <code>--web-dir desktop/workspace/dist</code>.</p></main></html>''',status_code=503,media_type='text/html',headers={'Cache-Control':'no-store'})
