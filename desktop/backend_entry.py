import sys

# The frozen executable can run only the explicitly bundled adapter module.
# It never interprets arbitrary scripts/modules or acts as the target interpreter.
if __name__ == '__main__':
    if sys.argv[1:] == ['--runtime-probe']:
        import json
        from termx.desktop.runtime import probe
        print(json.dumps(probe()))
    elif sys.argv[1:3] == ['--runtime-module', 'debugpy.adapter']:
        import runpy
        sys.argv = ['debugpy.adapter', *sys.argv[3:]]
        runpy.run_module('debugpy.adapter', run_name='__main__')
    else:
        from termx.desktop.runtime import configure_bundle
        configure_bundle()
        from termx.cli import main
        main()
