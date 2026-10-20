#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0603
#include <windows.h>
#include <windowsx.h>
#include <dwmapi.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "companion_bridge.h"

typedef struct {
    HWND window; uint32_t slot; HBITMAP bitmap; HDC dc; HGDIOBJ old;
    uint8_t *pixels; int width,height; uint64_t hash;
    double foot_x,foot_y,hit_x,hit_y,hit_width,hit_height; bool dragging,moved; POINT cursor_start,window_start;
} TXPet;
static TXPet pets[4];
static TXCompanionEvent events[64];static size_t read_event,write_event;
static void push(uint32_t slot,uint32_t kind,double x,double y) {
    size_t next=(write_event+1)%64;if(next==read_event)read_event=(read_event+1)%64;
    events[write_event]=(TXCompanionEvent){slot,kind,x,y};write_event=next;
}
static void release_bitmap(TXPet*p){
    if(p->dc){SelectObject(p->dc,p->old);DeleteDC(p->dc);p->dc=NULL;}
    if(p->bitmap){DeleteObject(p->bitmap);p->bitmap=NULL;}p->pixels=NULL;p->hash=0;
}
static LRESULT CALLBACK companion_proc(HWND hwnd,UINT msg,WPARAM wp,LPARAM lp){
    TXPet*p=(TXPet*)GetWindowLongPtrW(hwnd,GWLP_USERDATA);
    if(msg==WM_NCCREATE){p=(TXPet*)((CREATESTRUCTW*)lp)->lpCreateParams;SetWindowLongPtrW(hwnd,GWLP_USERDATA,(LONG_PTR)p);}
    if(!p)return DefWindowProcW(hwnd,msg,wp,lp);
    switch(msg){
        case WM_MOUSEACTIVATE:return MA_NOACTIVATE;
        case WM_NCHITTEST:{
            POINT point={GET_X_LPARAM(lp),GET_Y_LPARAM(lp)};ScreenToClient(hwnd,&point);
            if(point.x<p->hit_x||point.y<p->hit_y||point.x>=p->hit_x+p->hit_width||point.y>=p->hit_y+p->hit_height||point.x<0||point.y<0||point.x>=p->width||point.y>=p->height||!p->pixels||p->pixels[(point.y*p->width+point.x)*4+3]<20)return HTTRANSPARENT;
            return HTCLIENT;
        }
        case WM_LBUTTONDOWN:{RECT r;GetWindowRect(hwnd,&r);GetCursorPos(&p->cursor_start);p->window_start=(POINT){r.left,r.top};p->dragging=true;p->moved=false;SetCapture(hwnd);push(p->slot,2,0,0);return 0;}
        case WM_MOUSEMOVE:if(p->dragging){
            POINT mouse;GetCursorPos(&mouse);int dx=mouse.x-p->cursor_start.x,dy=mouse.y-p->cursor_start.y;
            if(abs(dx)>3||abs(dy)>3)p->moved=true;
            int x=p->window_start.x+dx,y=p->window_start.y+dy;
            SetWindowPos(hwnd,HWND_TOPMOST,x,y,0,0,SWP_NOSIZE|SWP_NOACTIVATE);
            push(p->slot,3,x+p->foot_x,y+p->foot_y);
        }return 0;
        case WM_LBUTTONUP:if(p->dragging){RECT r;GetWindowRect(hwnd,&r);p->dragging=false;ReleaseCapture();push(p->slot,4,r.left+p->foot_x,r.top+p->foot_y);if(!p->moved)push(p->slot,1,0,0);}return 0;
        case WM_CAPTURECHANGED:if(p->dragging){RECT r;GetWindowRect(hwnd,&r);p->dragging=false;push(p->slot,4,r.left+p->foot_x,r.top+p->foot_y);}return 0;
        case WM_RBUTTONUP:{
            HMENU menu=CreatePopupMenu();AppendMenuW(menu,MF_STRING,1,L"Open agent");AppendMenuW(menu,MF_STRING,5,L"Pause / resume");AppendMenuW(menu,MF_STRING,6,L"Bring home");AppendMenuW(menu,MF_STRING,7,L"Hide companion");
            POINT mouse;GetCursorPos(&mouse);UINT selected=TrackPopupMenu(menu,TPM_RETURNCMD|TPM_NONOTIFY|TPM_RIGHTBUTTON,mouse.x,mouse.y,0,hwnd,NULL);DestroyMenu(menu);if(selected)push(p->slot,selected,0,0);return 0;
        }
        case WM_DISPLAYCHANGE:release_bitmap(p);return 0;
        case WM_POWERBROADCAST:if(wp==PBT_APMSUSPEND)for(int i=0;i<4;i++)termx_companion_hide(i);return TRUE;
        default:return DefWindowProcW(hwnd,msg,wp,lp);
    }
}
bool termx_companion_available(void){return true;}
bool termx_companion_reduced_motion(void){BOOL enabled=TRUE;SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION,0,&enabled,0);return !enabled;}
bool termx_companion_awake(void){
    HDESK desktop=OpenInputDesktop(0,FALSE,DESKTOP_READOBJECTS);if(!desktop)return false;
    WCHAR name[64]={0};DWORD length=0;bool active=GetUserObjectInformationW(desktop,UOI_NAME,name,sizeof(name),&length)&&wcscmp(name,L"Default")==0;CloseDesktop(desktop);return active;
}
typedef struct {TXCompanionGeometry*out;size_t capacity,count;} TXInventory;
static BOOL CALLBACK monitor_geometry(HMONITOR monitor,HDC dc,LPRECT rect,LPARAM data){
    TXInventory*inventory=(TXInventory*)data;if(inventory->count>=inventory->capacity)return FALSE;
    MONITORINFO info;memset(&info,0,sizeof(info));info.cbSize=sizeof(info);if(!GetMonitorInfoW(monitor,&info))return TRUE;
    UINT dpiX=96,dpiY=96;HMODULE shcore=LoadLibraryW(L"Shcore.dll");
    if(shcore){typedef HRESULT(WINAPI*DpiFn)(HMONITOR,int,UINT*,UINT*);DpiFn getDpi=(DpiFn)GetProcAddress(shcore,"GetDpiForMonitor");if(getDpi)getDpi(monitor,0,&dpiX,&dpiY);FreeLibrary(shcore);}
    RECT r=info.rcWork;inventory->out[inventory->count++]=(TXCompanionGeometry){r.left,r.top,r.right-r.left,r.bottom-r.top,(double)dpiX/96.0,0};return TRUE;
}
static BOOL CALLBACK window_geometry(HWND window,LPARAM data){
    TXInventory*inventory=(TXInventory*)data;if(inventory->count>=inventory->capacity)return FALSE;
    LONG_PTR style=GetWindowLongPtrW(window,GWL_EXSTYLE);
    if(!IsWindowVisible(window)||IsIconic(window)||(style&WS_EX_TOOLWINDOW))return TRUE;
    DWORD cloaked=0;if(SUCCEEDED(DwmGetWindowAttribute(window,DWMWA_CLOAKED,&cloaked,sizeof(cloaked)))&&cloaked)return TRUE;
    RECT r;if(FAILED(DwmGetWindowAttribute(window,DWMWA_EXTENDED_FRAME_BOUNDS,&r,sizeof(r)))&&!GetWindowRect(window,&r))return TRUE;
    if(r.right-r.left<60||r.bottom-r.top<40)return TRUE;
    inventory->out[inventory->count++]=(TXCompanionGeometry){r.left,r.top,r.right-r.left,r.bottom-r.top,1,1};return TRUE;
}
size_t termx_companion_geometry(TXCompanionGeometry*out,size_t capacity){
    if(!out)return 0;TXInventory inventory={out,capacity,0};EnumDisplayMonitors(NULL,NULL,monitor_geometry,(LPARAM)&inventory);EnumWindows(window_geometry,(LPARAM)&inventory);return inventory.count;
}
bool termx_companion_render(uint32_t slot,double x,double y,double width,double height,double anchor_x,double anchor_y,double hit_x,double hit_y,double hit_width,double hit_height,const uint8_t*rgba,uint32_t pw,uint32_t ph,bool flip){
    if(slot>=4||!rgba||pw==0||ph==0||pw>512||ph>512||width<1||height<1||width>2048||height>2048)return false;
    TXPet*p=&pets[slot];p->slot=slot;p->foot_x=anchor_x;p->foot_y=anchor_y;p->hit_x=hit_x;p->hit_y=hit_y;p->hit_width=hit_width;p->hit_height=hit_height;
    if(!p->window){
        static bool registered=false;
        if(!registered){WNDCLASSEXW cls;memset(&cls,0,sizeof(cls));cls.cbSize=sizeof(cls);cls.lpfnWndProc=companion_proc;cls.hInstance=GetModuleHandleW(NULL);cls.lpszClassName=L"TermXNativeCompanion";cls.hCursor=LoadCursor(NULL,IDC_HAND);if(!RegisterClassExW(&cls)&&GetLastError()!=ERROR_CLASS_ALREADY_EXISTS)return false;registered=true;}
        p->window=CreateWindowExW(WS_EX_LAYERED|WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW|WS_EX_TOPMOST,L"TermXNativeCompanion",L"TermX companion",WS_POPUP,(int)x,(int)y,(int)width,(int)height,NULL,NULL,GetModuleHandleW(NULL),p);if(!p->window)return false;
    }
    int w=(int)ceil(width),h=(int)ceil(height);uint64_t hash=1469598103934665603ULL;
    for(size_t i=0;i<(size_t)pw*ph*4;i++)hash=(hash^rgba[i])*1099511628211ULL;
    hash^=(uint64_t)flip;
    bool changed=p->hash!=hash||p->width!=w||p->height!=h;
    if(changed){
        if(p->width!=w||p->height!=h||!p->bitmap){
            release_bitmap(p);BITMAPINFO info;memset(&info,0,sizeof(info));info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);info.bmiHeader.biWidth=w;info.bmiHeader.biHeight=-h;info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
            p->dc=CreateCompatibleDC(NULL);p->bitmap=CreateDIBSection(p->dc,&info,DIB_RGB_COLORS,(void**)&p->pixels,NULL,0);
            if(!p->dc||!p->bitmap){release_bitmap(p);return false;}p->old=SelectObject(p->dc,p->bitmap);p->width=w;p->height=h;
        }
        for(int row=0;row<h;row++)for(int col=0;col<w;col++){
            uint32_t sx=(uint32_t)((uint64_t)col*pw/w),sy=(uint32_t)((uint64_t)row*ph/h);if(flip)sx=pw-1-sx;
            const uint8_t*src=rgba+((size_t)sy*pw+sx)*4;uint8_t*dst=p->pixels+((size_t)row*w+col)*4;uint32_t a=src[3];
            dst[0]=(uint8_t)(src[2]*a/255);dst[1]=(uint8_t)(src[1]*a/255);dst[2]=(uint8_t)(src[0]*a/255);dst[3]=(uint8_t)a;
        }
        p->hash=hash;POINT origin={0,0},dest={(int)x,(int)y};SIZE size={w,h};BLENDFUNCTION blend={AC_SRC_OVER,0,255,AC_SRC_ALPHA};
        if(p->dragging){RECT r;GetWindowRect(p->window,&r);dest=(POINT){r.left,r.top};}
        if(!UpdateLayeredWindow(p->window,NULL,&dest,&size,p->dc,&origin,0,&blend,ULW_ALPHA))return false;
    }else if(!p->dragging)SetWindowPos(p->window,HWND_TOPMOST,(int)x,(int)y,w,h,SWP_NOACTIVATE);
    ShowWindow(p->window,SW_SHOWNOACTIVATE);
return true;
}
void termx_companion_hide(uint32_t slot){
    if(slot>=4)return;TXPet*p=&pets[slot];p->dragging=false;
    if(p->window&&GetCapture()==p->window)ReleaseCapture();
    if(p->window)ShowWindow(p->window,SW_HIDE);
    for(size_t i=read_event;i!=write_event;i=(i+1)%64)if(events[i].slot==slot)events[i].kind=0;
}
bool termx_companion_event(TXCompanionEvent*out){if(!out||read_event==write_event)return false;*out=events[read_event];read_event=(read_event+1)%64;return true;}
void termx_companion_shutdown(void){for(int i=0;i<4;i++){if(pets[i].window)DestroyWindow(pets[i].window);release_bitmap(&pets[i]);memset(&pets[i],0,sizeof(TXPet));}read_event=write_event=0;}
