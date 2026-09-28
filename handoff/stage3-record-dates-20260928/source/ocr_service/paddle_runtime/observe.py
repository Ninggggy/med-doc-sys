"""Local SDK instrumentation: observes real arrays without changing model inputs."""
import time


def shapes(value):
    if hasattr(value,'shape'):return list(value.shape)
    if isinstance(value,(list,tuple)):return [shapes(v) for v in value]
    if isinstance(value,dict):return {k:shapes(v) for k,v in value.items() if hasattr(v,'shape') or isinstance(v,(list,tuple))}
    return type(value).__name__


def instrument(adapter):
    if hasattr(adapter,'_shape_events'):return adapter._shape_events
    events=[]
    if hasattr(adapter.model,'paddlex_pipeline'):
        p=adapter.model.paddlex_pipeline
        modules=[('detection',p.text_det_model),('recognition',p.text_rec_model)]
    else:modules=[('recognition',adapter.model.paddlex_predictor)]
    for label,module in modules:
        for key,op in list(module.pre_tfs.items()):
            def wrapped(*args,_op=op,_key=key,_label=label,**kwargs):
                t=time.monotonic();out=_op(*args,**kwargs)
                events.append(dict(module=_label,operation=_key,input_shapes=shapes(kwargs if kwargs else args),
                    output_shapes=shapes(out),seconds=time.monotonic()-t))
                return out
            module.pre_tfs[key]=wrapped
        original=module.runner
        def inference(*args,_original=original,_label=label,**kwargs):
            t=time.monotonic();out=_original(*args,**kwargs)
            events.append(dict(module=_label,operation='network_inference',input_shapes=shapes(kwargs if kwargs else args),
                output_shapes=shapes(out),seconds=time.monotonic()-t))
            return out
        module.runner=inference
    adapter._shape_events=events
    return events
