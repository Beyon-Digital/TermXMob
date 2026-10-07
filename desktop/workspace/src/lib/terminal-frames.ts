// PTY output is binary. Text frames describe shell state and must not become
// visible shell input/output or leak transport JSON into the user's terminal.
export function terminalFrame(value:unknown):{output?:Uint8Array|string;exit?:number;error?:string}{
 if(Object.prototype.toString.call(value)==='[object ArrayBuffer]')return {output:new Uint8Array(value as ArrayBuffer)};
 if(typeof value!=='string')return {};
 try{const control=JSON.parse(value);if(control&&typeof control==='object'&&typeof control.type==='string'){
  if(control.type==='exit')return {exit:typeof control.code==='number'?control.code:0};
  if(control.type==='error')return {error:String(control.message||'Terminal operation failed')};
  return {};
 }}catch{}
 return {output:value};
}
