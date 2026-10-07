import {it,expect} from 'vitest';
import {fileUri,relativeFileUri} from './lsp';
it('opens same-root definitions using POSIX, Windows drive and UNC file URIs',()=>{
 expect(fileUri('/project with spaces','src/main.py')).toBe('file:///project%20with%20spaces/src/main.py');
 expect(relativeFileUri('/project with spaces','file:///project%20with%20spaces/src/main.py')).toBe('src/main.py');
 expect(fileUri('C:\\Users\\Owner\\Project','src\\main.ts')).toBe('file:///C:/Users/Owner/Project/src/main.ts');
 expect(relativeFileUri('C:\\Users\\Owner\\Project','file:///c:/users/owner/project/src/Main.ts')).toBe('src/Main.ts');
 expect(fileUri('\\\\server\\share\\project','main.ts')).toBe('file://server/share/project/main.ts');
 expect(relativeFileUri('\\\\server\\share\\project','file://server/share/project/main.ts')).toBe('main.ts');
});
it('rejects sibling roots, another drive, network authority, malformed escapes and encoded traversal',()=>{
 for(const uri of ['file:///project-other/main.py','file:///other/main.py','https://host/project/main.py','file://other/project/main.py','file:///project/%ZZ','file:///project/%2e%2e/secret.py'])expect(relativeFileUri('/project',uri)).toBeNull();
 expect(relativeFileUri('C:\\project','file:///D:/project/main.ts')).toBeNull();
});
