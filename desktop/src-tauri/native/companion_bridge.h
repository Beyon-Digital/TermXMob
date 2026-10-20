#ifndef TERMX_COMPANION_BRIDGE_H
#define TERMX_COMPANION_BRIDGE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
// All functions must run on the app's main thread. Pointer arguments are
// borrowed for the duration of the call; native renderers copy image bytes.
typedef struct { double x,y,width,height,scale; uint32_t kind; } TXCompanionGeometry;
typedef struct { uint32_t slot,kind; double x,y; } TXCompanionEvent;
bool termx_companion_available(void);
bool termx_companion_awake(void);
bool termx_companion_reduced_motion(void);
size_t termx_companion_geometry(TXCompanionGeometry *out,size_t capacity);
bool termx_companion_render(uint32_t slot,double x,double y,double width,double height,
    double anchor_x,double anchor_y,double hit_x,double hit_y,double hit_width,double hit_height,const uint8_t *rgba,uint32_t pixel_width,uint32_t pixel_height,bool flip);
void termx_companion_hide(uint32_t slot);
bool termx_companion_event(TXCompanionEvent *out);
void termx_companion_shutdown(void);
#endif
