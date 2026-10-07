import {it,expect} from 'vitest';
import {terminalFrame} from './terminal-frames';
it('renders exact shell bytes while consuming cwd/activity/exit transport events',()=>{
 const bytes=new TextEncoder().encode('hello\r\n');expect(Array.from(terminalFrame(bytes.buffer).output as Uint8Array)).toEqual(Array.from(bytes));
 expect(terminalFrame('{"type":"cwd","cwd":"/workspace"}')).toEqual({});
 expect(terminalFrame('{"type":"activity","state":"busy"}')).toEqual({});
 expect(terminalFrame('{"type":"exit","code":3}')).toEqual({exit:3});
 expect(terminalFrame('plain legacy text')).toEqual({output:'plain legacy text'});
});
