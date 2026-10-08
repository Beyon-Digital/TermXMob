import sys

# The frozen executable can run only explicitly bundled runtime modules.
# It never interprets arbitrary scripts/modules or acts as the target interpreter.
if __name__ == '__main__':
    if sys.argv[1:] == ['--runtime-probe']:
        import json
        from termx.desktop.runtime import probe
        print(json.dumps(probe()))
    elif sys.argv[1:] == ['--runtime-smoke']:
        import asyncio
        import json
        from termx.desktop.runtime_smoke import smoke
        print(json.dumps(asyncio.run(smoke())))
    elif sys.argv[1:3] == ['--runtime-module', 'debugpy.adapter']:
        import runpy
        sys.argv = ['debugpy.adapter', *sys.argv[3:]]
        runpy.run_module('debugpy.adapter', run_name='__main__')
    elif sys.argv[1:] == ['--runtime-module', 'termx.sandbox._win_conpty_worker']:
        from termx.sandbox._win_conpty_worker import main
        main()
    elif sys.argv[1:3] == ['--runtime-module', 'termx.sandbox._win_shim'] and len(sys.argv) == 4:
        sys.argv = ['termx.sandbox._win_shim', sys.argv[3]]
        from termx.sandbox._win_shim import main
        main()
    else:
        from termx.desktop.runtime import configure_bundle
        configure_bundle()
        from termx.cli import main
        main()
