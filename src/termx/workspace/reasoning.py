"""Reasoning selectors from the installed engine's model-specific catalogue."""
from copy import deepcopy


def options(configuration,model):
    variants=configuration.get('model_configurations',{})
    if model is not None:
        selected=variants.get(model,{})
    else:selected=configuration
    return [deepcopy(item) for item in selected.get('config_options',[]) if item.get('category') in {'thought_level','reasoning','effort'} and item.get('type')=='select']


def validate(state,engine,model,selections):
    if selections is None:selections={}
    if not isinstance(selections,dict) or len(selections)>8 or any(not isinstance(k,str) or len(k)>200 or not isinstance(v,str) or len(v)>200 for k,v in selections.items()):
        raise ValueError('Reasoning settings must map at most 8 advertised option IDs to values')
    if not selections:return {}
    catalogue=getattr(getattr(state,'engines',None),'catalogue',None)
    entry=catalogue.entries.get(engine,{}) if catalogue else {}
    if entry.get('stale') or entry.get('refresh_error'):
        raise ValueError('Refresh this engine catalogue before selecting reasoning')
    advertised={option['id']:{item['value'] for item in option.get('options',[]) if isinstance(item,dict) and isinstance(item.get('value'),str)} for option in options(entry.get('configuration',{}),model)}
    if any(key not in advertised or value not in advertised[key] for key,value in selections.items()):
        raise ValueError('Reasoning selection is not advertised for this engine/model')
    return dict(selections)
