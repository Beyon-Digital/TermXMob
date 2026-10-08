/** Plain Tab and interrupt keys remain terminal input. Shift+Tab provides
 * a visible, documented way back to the adjacent workspace toolbar. */
export function terminalKeyHandler(leave:()=>void) {
  return (event:KeyboardEvent) => {
    if(event.key==='Tab' && event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
      if(event.type==='keydown'){event.preventDefault();leave()}
      return false;
    }
    return true;
  };
}
