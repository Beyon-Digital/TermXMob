import type {ShellEngine} from '../lib/shell-engine';
import type {LayoutController, LayoutDeps} from './layout-controller';
import {useLegacyLayout} from './use-legacy-layout';
import {useTreeLayout} from './use-tree-layout';

/**
 * The layout engine for this shell: the old dock or the tab-group tree, behind one `LayoutController` interface.
 *
 * `engine` is fixed for the life of the component that calls this: the shell body is rendered with `key={engine}`, so a
 * change of engine remounts it instead of changing which hooks run. That keeps the call below unconditional in practice.
 */
export function useLayoutController(engine: ShellEngine, deps: LayoutDeps): LayoutController {
  return engine === 'tree' ? useTreeLayout(deps) : useLegacyLayout(deps);
}
