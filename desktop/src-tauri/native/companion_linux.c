#include <gtk/gtk.h>
#include <gdk/gdkx.h>
#include <X11/Xatom.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "companion_bridge.h"

typedef struct {GtkWidget*window;GtkWidget*menu;cairo_surface_t*surface;uint8_t*pixels;uint32_t slot,pw,ph;int width,height;uint64_t hash;bool dragging,moved;double foot_x,foot_y,start_x,start_y;int origin_x,origin_y;} TXPet;
static TXPet pets[4];static TXCompanionEvent events[64];static size_t read_event,write_event;static bool tx_sleeping=false,tx_freedesktop_locked=false,tx_gnome_locked=false;static bool subscribed=false;
static void push(uint32_t slot,uint32_t kind,double x,double y){size_t next=(write_event+1)%64;if(next==read_event)read_event=(read_event+1)%64;events[write_event]=(TXCompanionEvent){slot,kind,x,y};write_event=next;}
static void lock_signal(GDBusConnection*c,const gchar*s,const gchar*p,const gchar*i,const gchar*n,GVariant*args,gpointer data){
    gboolean active=FALSE;
    if(g_variant_is_of_type(args,G_VARIANT_TYPE("(b)"))){
        g_variant_get(args,"(b)",&active);
        if(strcmp(i,"org.freedesktop.login1.Manager")==0)tx_sleeping=active;
        else if(strcmp(i,"org.gnome.ScreenSaver")==0)tx_gnome_locked=active;
        else tx_freedesktop_locked=active;
        if(tx_sleeping||tx_freedesktop_locked||tx_gnome_locked)for(int j=0;j<4;j++)termx_companion_hide(j);
    }
}
static void subscribe(void){
    if(subscribed)return;subscribed=true;
    GDBusConnection*session=g_bus_get_sync(G_BUS_TYPE_SESSION,NULL,NULL);
    if(session){g_dbus_connection_signal_subscribe(session,NULL,"org.freedesktop.ScreenSaver","ActiveChanged",NULL,NULL,G_DBUS_SIGNAL_FLAGS_NONE,lock_signal,NULL,NULL);g_dbus_connection_signal_subscribe(session,NULL,"org.gnome.ScreenSaver","ActiveChanged",NULL,NULL,G_DBUS_SIGNAL_FLAGS_NONE,lock_signal,NULL,NULL);const char*names[]={"org.freedesktop.ScreenSaver","org.gnome.ScreenSaver"};const char*paths[]={"/org/freedesktop/ScreenSaver","/org/gnome/ScreenSaver"};
        for(int i=0;i<2;i++){GVariant*reply=g_dbus_connection_call_sync(session,names[i],paths[i],names[i],"GetActive",NULL,G_VARIANT_TYPE("(b)"),G_DBUS_CALL_FLAGS_NONE,200,NULL,NULL);if(reply){gboolean active;g_variant_get(reply,"(b)",&active);if(i==0)tx_freedesktop_locked=active;else tx_gnome_locked=active;g_variant_unref(reply);}}
        g_object_unref(session);}
    GDBusConnection*system=g_bus_get_sync(G_BUS_TYPE_SYSTEM,NULL,NULL);
    if(system){g_dbus_connection_signal_subscribe(system,"org.freedesktop.login1","org.freedesktop.login1.Manager","PrepareForSleep",NULL,NULL,G_DBUS_SIGNAL_FLAGS_NONE,lock_signal,NULL,NULL);g_object_unref(system);}
}
bool termx_companion_available(void){GdkDisplay*d=gdk_display_get_default();return d&&GDK_IS_X11_DISPLAY(d)&&gdk_screen_is_composited(gdk_screen_get_default())&&gdk_screen_get_rgba_visual(gdk_screen_get_default());}
bool termx_companion_reduced_motion(void){gboolean enabled=TRUE;GtkSettings*settings=gtk_settings_get_default();if(settings)g_object_get(settings,"gtk-enable-animations",&enabled,NULL);return !enabled;}
bool termx_companion_awake(void){subscribe();return !tx_sleeping&&!tx_freedesktop_locked&&!tx_gnome_locked&&termx_companion_available();}
static gboolean draw(GtkWidget*widget,cairo_t*cr,gpointer data){
    TXPet*p=data;cairo_set_operator(cr,CAIRO_OPERATOR_SOURCE);cairo_set_source_rgba(cr,0,0,0,0);cairo_paint(cr);
    if(p->surface){cairo_set_operator(cr,CAIRO_OPERATOR_OVER);cairo_scale(cr,(double)p->width/p->pw,(double)p->height/p->ph);cairo_set_source_surface(cr,p->surface,0,0);cairo_pattern_set_filter(cairo_get_source(cr),CAIRO_FILTER_NEAREST);cairo_paint(cr);}return TRUE;
}
static void menu_action(GtkMenuItem*item,gpointer data){TXPet*p=data;push(p->slot,GPOINTER_TO_UINT(g_object_get_data(G_OBJECT(item),"termx-action")),0,0);}
static gboolean press(GtkWidget*widget,GdkEventButton*event,gpointer data){
    TXPet*p=data;if(event->button==1){p->dragging=true;p->moved=false;p->start_x=event->x_root;p->start_y=event->y_root;gtk_window_get_position(GTK_WINDOW(widget),&p->origin_x,&p->origin_y);gtk_grab_add(widget);push(p->slot,2,0,0);return TRUE;}
    if(event->button==3){GtkWidget*menu=gtk_menu_new();p->menu=menu;g_object_add_weak_pointer(G_OBJECT(menu),(gpointer*)&p->menu);const char*titles[]={"Open agent","Pause / resume","Bring home","Hide companion"};int ids[]={1,5,6,7};
        for(int i=0;i<4;i++){GtkWidget*item=gtk_menu_item_new_with_label(titles[i]);g_object_set_data(G_OBJECT(item),"termx-action",GUINT_TO_POINTER(ids[i]));g_signal_connect(item,"activate",G_CALLBACK(menu_action),p);gtk_menu_shell_append(GTK_MENU_SHELL(menu),item);}
        g_signal_connect_swapped(menu,"selection-done",G_CALLBACK(gtk_widget_destroy),menu);gtk_widget_show_all(menu);gtk_menu_popup_at_pointer(GTK_MENU(menu),(GdkEvent*)event);return TRUE;}
    return FALSE;
}
static gboolean motion(GtkWidget*widget,GdkEventMotion*event,gpointer data){TXPet*p=data;if(!p->dragging)return FALSE;double dx=event->x_root-p->start_x,dy=event->y_root-p->start_y;if(fabs(dx)>3||fabs(dy)>3)p->moved=true;int x=p->origin_x+(int)dx,y=p->origin_y+(int)dy;gtk_window_move(GTK_WINDOW(widget),x,y);push(p->slot,3,x+p->foot_x,y+p->foot_y);return TRUE;}
static gboolean release(GtkWidget*widget,GdkEventButton*event,gpointer data){TXPet*p=data;if(!p->dragging||event->button!=1)return FALSE;p->dragging=false;gtk_grab_remove(widget);int x,y;gtk_window_get_position(GTK_WINDOW(widget),&x,&y);push(p->slot,4,x+p->foot_x,y+p->foot_y);if(!p->moved)push(p->slot,1,0,0);return TRUE;}
static unsigned long property(Display*d,Window w,Atom name,Atom expected,unsigned char**data){
    Atom actual=None;int format=0;unsigned long count=0,left=0;*data=NULL;
    int result=XGetWindowProperty(d,w,name,0,4096,False,expected,&actual,&format,&count,&left,data);
    if(result!=Success||actual!=expected||format!=32||left!=0){if(*data)XFree(*data);*data=NULL;return 0;}
    return count;
}
size_t termx_companion_geometry(TXCompanionGeometry*out,size_t capacity){
    if(!out||!termx_companion_available())return 0;size_t count=0;GdkDisplay*gdisplay=gdk_display_get_default();
    for(int i=0;i<gdk_display_get_n_monitors(gdisplay)&&count<capacity;i++){GdkRectangle r;gdk_monitor_get_workarea(gdk_display_get_monitor(gdisplay,i),&r);out[count++]=(TXCompanionGeometry){r.x,r.y,r.width,r.height,1,0};}
    Display*d=gdk_x11_display_get_xdisplay(gdisplay);Window root=DefaultRootWindow(d);unsigned char*clients=NULL;double scale=gdk_window_get_scale_factor(gdk_get_default_root_window());
    gdk_x11_display_error_trap_push(gdisplay);
    unsigned long windows=property(d,root,XInternAtom(d,"_NET_CLIENT_LIST_STACKING",False),XA_WINDOW,&clients);
    if(clients){Window*list=(Window*)clients;for(unsigned long index=windows;index>0&&count<capacity;index--){
        Window window=list[index-1];XWindowAttributes attrs;if(!XGetWindowAttributes(d,window,&attrs)||attrs.map_state!=IsViewable||attrs.width<60||attrs.height<40)continue;
        bool ours=false;for(int slot=0;slot<4;slot++)if(pets[slot].window){GdkWindow*native=gtk_widget_get_window(pets[slot].window);if(native&&GDK_WINDOW_XID(native)==window)ours=true;}if(ours)continue;
        unsigned char*types=NULL;unsigned long ntypes=property(d,window,XInternAtom(d,"_NET_WM_WINDOW_TYPE",False),XA_ATOM,&types);bool normal=true;Atom dock=XInternAtom(d,"_NET_WM_WINDOW_TYPE_DOCK",False),desktop=XInternAtom(d,"_NET_WM_WINDOW_TYPE_DESKTOP",False);
        if(types){for(unsigned long i=0;i<ntypes;i++)if(((Atom*)types)[i]==dock||((Atom*)types)[i]==desktop)normal=false;XFree(types);}if(!normal)continue;
        int x,y;Window child;if(!XTranslateCoordinates(d,window,root,0,0,&x,&y,&child))continue;
        unsigned char*extents=NULL;unsigned long fields=property(d,window,XInternAtom(d,"_NET_FRAME_EXTENTS",False),XA_CARDINAL,&extents);
        unsigned long l=0,r=0,t=0,b=0;if(fields==4&&extents){unsigned long*e=(unsigned long*)extents;if(e[0]<=256&&e[1]<=256&&e[2]<=256&&e[3]<=256){l=e[0];r=e[1];t=e[2];b=e[3];}}if(extents)XFree(extents);
        out[count++]=(TXCompanionGeometry){(x-(double)l)/scale,(y-(double)t)/scale,(attrs.width+(double)l+r)/scale,(attrs.height+(double)t+b)/scale,1,1};
    }XFree(clients);}
    gdk_x11_display_error_trap_pop_ignored(gdisplay);return count;
}
bool termx_companion_render(uint32_t slot,double x,double y,double width,double height,double anchor_x,double anchor_y,double hit_x,double hit_y,double hit_width,double hit_height,const uint8_t*rgba,uint32_t pw,uint32_t ph,bool flip){
    if(slot>=4||!rgba||!pw||!ph||pw>512||ph>512||width<1||height<1||width>2048||height>2048)return false;TXPet*p=&pets[slot];p->slot=slot;p->foot_x=anchor_x;p->foot_y=anchor_y;
    if(!p->window){
        p->window=gtk_window_new(GTK_WINDOW_TOPLEVEL);if(!p->window)return false;gtk_window_set_title(GTK_WINDOW(p->window),"TermX companion");gtk_window_set_decorated(GTK_WINDOW(p->window),FALSE);gtk_window_set_accept_focus(GTK_WINDOW(p->window),FALSE);gtk_window_set_focus_on_map(GTK_WINDOW(p->window),FALSE);gtk_window_set_keep_above(GTK_WINDOW(p->window),TRUE);gtk_window_set_skip_taskbar_hint(GTK_WINDOW(p->window),TRUE);gtk_window_set_skip_pager_hint(GTK_WINDOW(p->window),TRUE);gtk_window_set_resizable(GTK_WINDOW(p->window),FALSE);gtk_window_stick(GTK_WINDOW(p->window));gtk_window_set_type_hint(GTK_WINDOW(p->window),GDK_WINDOW_TYPE_HINT_UTILITY);
        gtk_widget_set_visual(p->window,gdk_screen_get_rgba_visual(gdk_screen_get_default()));gtk_widget_set_app_paintable(p->window,TRUE);gtk_widget_add_events(p->window,GDK_BUTTON_PRESS_MASK|GDK_BUTTON_RELEASE_MASK|GDK_POINTER_MOTION_MASK);
        g_signal_connect(p->window,"draw",G_CALLBACK(draw),p);g_signal_connect(p->window,"button-press-event",G_CALLBACK(press),p);g_signal_connect(p->window,"button-release-event",G_CALLBACK(release),p);g_signal_connect(p->window,"motion-notify-event",G_CALLBACK(motion),p);
    }
    int w=(int)ceil(width),h=(int)ceil(height);uint64_t hash=1469598103934665603ULL;for(size_t i=0;i<(size_t)pw*ph*4;i++)hash=(hash^rgba[i])*1099511628211ULL;hash^=(uint64_t)flip;hash^=(uint64_t)(hit_x*11+hit_y*17+hit_width*31+hit_height*53);
    if(p->hash!=hash||p->width!=w||p->height!=h||p->pw!=pw||p->ph!=ph){
        if(p->surface)cairo_surface_destroy(p->surface);free(p->pixels);p->pixels=malloc((size_t)pw*ph*4);if(!p->pixels){p->surface=NULL;return false;}
        for(uint32_t row=0;row<ph;row++)for(uint32_t col=0;col<pw;col++){
            const uint8_t*src=rgba+((size_t)row*pw+(flip?pw-1-col:col))*4;uint8_t*dst=p->pixels+((size_t)row*pw+col)*4;uint32_t a=src[3];dst[0]=(uint8_t)(src[2]*a/255);dst[1]=(uint8_t)(src[1]*a/255);dst[2]=(uint8_t)(src[0]*a/255);dst[3]=(uint8_t)a;
        }
        p->surface=cairo_image_surface_create_for_data(p->pixels,CAIRO_FORMAT_ARGB32,pw,ph,pw*4);if(cairo_surface_status(p->surface)!=CAIRO_STATUS_SUCCESS)return false;p->pw=pw;p->ph=ph;p->width=w;p->height=h;p->hash=hash;
        gtk_widget_set_size_request(p->window,w,h);gtk_window_resize(GTK_WINDOW(p->window),w,h);gtk_widget_realize(p->window);
        // Set an X11 input region made only of nontransparent sprite pixels.
        // Desktop clicks outside this silhouette pass straight through.
        cairo_region_t*region=cairo_region_create();for(int row=0;row<h;row++){int start=-1;for(int col=0;col<=w;col++){
            bool opaque=col<w&&col>=hit_x&&col<hit_x+hit_width&&row>=hit_y&&row<hit_y+hit_height&&p->pixels[(((size_t)row*ph/h)*pw+(size_t)col*pw/w)*4+3]>20;
            if(opaque&&start<0)start=col;if(!opaque&&start>=0){cairo_rectangle_int_t r={start,row,col-start,1};cairo_region_union_rectangle(region,&r);start=-1;}
        }}gtk_widget_input_shape_combine_region(p->window,region);cairo_region_destroy(region);gtk_widget_queue_draw(p->window);
    }
    if(!p->dragging)gtk_window_move(GTK_WINDOW(p->window),(int)x,(int)y);gtk_widget_show(p->window);
return true;
}
void termx_companion_hide(uint32_t slot){
    if(slot>=4)return;TXPet*p=&pets[slot];
    if(p->dragging&&p->window){gtk_grab_remove(p->window);p->dragging=false;}
    if(p->menu&&GTK_IS_MENU(p->menu)){GtkWidget*menu=p->menu;g_object_remove_weak_pointer(G_OBJECT(menu),(gpointer*)&p->menu);p->menu=NULL;gtk_menu_popdown(GTK_MENU(menu));}
    if(p->window)gtk_widget_hide(p->window);
    for(size_t i=read_event;i!=write_event;i=(i+1)%64)if(events[i].slot==slot)events[i].kind=0;
}
bool termx_companion_event(TXCompanionEvent*out){if(!out||read_event==write_event)return false;*out=events[read_event];read_event=(read_event+1)%64;return true;}
void termx_companion_shutdown(void){for(int i=0;i<4;i++){if(pets[i].window)gtk_widget_destroy(pets[i].window);if(pets[i].surface)cairo_surface_destroy(pets[i].surface);free(pets[i].pixels);memset(&pets[i],0,sizeof(TXPet));}read_event=write_event=0;}
