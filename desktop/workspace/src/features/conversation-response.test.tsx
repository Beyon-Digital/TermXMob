import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import ConversationResponse,{conversationFileReference} from './ConversationResponse';
import {transfer} from '../lib/transfers';
vi.mock('../lib/transfers',()=>({transfer:vi.fn(),download:vi.fn()}));
afterEach(cleanup);
it('resolves exact file references and line anchors without turning URLs or commands into editor actions',()=>{
 expect(conversationFileReference('src/App.tsx:42:3')).toEqual({path:'src/App.tsx',line:42});
 expect(conversationFileReference('/workspace/main.py#L12-L20')).toEqual({path:'/workspace/main.py',line:12});
 expect(conversationFileReference('Dockerfile')).toEqual({path:'Dockerfile'});
 for(const text of ['https://example.com/file.ts','api/users','process.env','npm run build','../outside.ts','src/test.ts:0','javascript:alert(1)','//server/file.ts'])expect(conversationFileReference(text)).toBeNull();
});
it('opens inline file references and local Markdown links while preserving fenced code and external links',()=>{
 const open=vi.fn();render(<ConversationResponse onOpenFile={open}>{'Update `src/App.tsx:12` and [the config](config.json#L3). See [docs](https://example.com/docs).\n\n```ts\nsrc/fenced.ts\n```'}</ConversationResponse>);
 fireEvent.click(screen.getByRole('button',{name:'Open file src/App.tsx:12'}));expect(open).toHaveBeenLastCalledWith('src/App.tsx',12);
 fireEvent.click(screen.getByRole('button',{name:'Open file config.json:3'}));expect(open).toHaveBeenLastCalledWith('config.json',3);
 expect(screen.getByRole('button',{name:'docs'})).toHaveAttribute('data-streamdown','link');
 expect(screen.queryByRole('button',{name:'Open file src/fenced.ts'})).not.toBeInTheDocument();expect(open).toHaveBeenCalledTimes(2);
});
it('updates the file opener when session context changes without losing the rendered message',()=>{
 const first=vi.fn(),second=vi.fn(),text='Inspect `src/App.tsx`';const view=render(<ConversationResponse onOpenFile={first}>{text}</ConversationResponse>);
 view.rerender(<ConversationResponse onOpenFile={second}>{text}</ConversationResponse>);fireEvent.click(screen.getByRole('button',{name:'Open file src/App.tsx'}));expect(first).not.toHaveBeenCalled();expect(second).toHaveBeenCalledWith('src/App.tsx',undefined);
});

it('uses a source-labeled card for a standalone actual web link without fetching metadata',()=>{
 render(<ConversationResponse>{'[Project documentation](https://example.test/docs)'}</ConversationResponse>);
 const link=screen.getByRole('link',{name:/Project documentation/});expect(link).toHaveAttribute('href','https://example.test/docs');expect(link).toHaveTextContent('example.test');expect(link).toHaveTextContent('Link in response');expect(transfer).not.toHaveBeenCalled();
});
it('does not load remote images or execute raw HTML, SVG, scripts or unsafe URL schemes',()=>{
 const {container}=render(<ConversationResponse>{'![External illustration](https://tracker.test/pixel.png)\n\n<script>alert(1)</script>\n\n<svg onload="alert(1)"></svg>\n\n[Unsafe](javascript:alert(1))'}</ConversationResponse>);
 expect(screen.getByRole('link',{name:/External illustration/})).toHaveTextContent('preview not loaded');expect(container.querySelector('img,iframe,object,embed,script,svg[onload]')).toBeNull();expect(container.querySelector('[href^="javascript:"],[src^="data:"]')).toBeNull();expect(transfer).not.toHaveBeenCalled();
});
it('offers an owned Markdown image preview only through an explicit authorized load',()=>{
 render(<ConversationResponse>{'![Owned diagram](/api/workspace/tasks/task/artifacts/image)'}</ConversationResponse>);
 expect(screen.getByRole('button',{name:'Load image preview'})).toBeVisible();expect(screen.queryByRole('img')).toBeNull();expect(transfer).not.toHaveBeenCalled();
});
