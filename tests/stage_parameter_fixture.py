"""Named field descriptors for isolated Qt and native receipt tests."""
import copy
from prompt_calculus_studio.stage_parameters import empty, identity


def profile_fields(profile):
    graph=profile['graph']
    nodes=[]
    for ident,raw in graph.items():
        fields=[];sources=[]
        for name,value in raw['inputs'].items():
            if isinstance(value,list):sources.append(dict(input=name,node=str(value[0]),slot=value[1]));continue
            field=dict(field=name,value=value,editable=True,reason='',schema=raw['class_type']+':'+name)
            if name in ('sampler_name','scheduler','upscale_method','crop'):
                field.update(type='ENUM',choices=[value,'alternate'])
            else:field['type']='BOOLEAN' if type(value) is bool else 'INT' if type(value) is int else 'FLOAT' if type(value) is float else 'STRING'
            if name in ('width','height'):field.update(min=16,max=4096,step=8,enforce_step=True)
            if name=='batch_size':field.update(min=1,max=4096)
            if name in ('seed','noise_seed'):field.update(min=0,max=2**50,seed_mode='fixed',seed_timing='after')
            if name=='cfg':field.update(min=0,max=100)
            fields.append(field)
        nodes.append(dict(id=ident,path=[ident],class_type=raw['class_type'],title=raw.get('_meta',{}).get('title',raw['class_type']),
                          mode=0,fields=fields,sources=sources))
    return dict(version=1,identity=identity(profile),nodes=nodes)


def intention(profile,node='35',field='cfg',value=3):
    config=empty(profile)
    item=next(n for n in profile_fields(profile)['nodes'] if n['id']==node)
    descriptor=next(f for f in item['fields'] if f['field']==field)
    patch=copy.deepcopy(descriptor);patch.pop('editable');patch.pop('reason');patch.pop('seed_mode',None);patch.pop('seed_timing',None)
    patch.update(node=node,path=[node],class_type=item['class_type'],base=descriptor['value'],value=value)
    config['patches']=[patch];return config


def add_samplers(profile):
    for ident in ('35','58'):
        profile['graph'][ident]=dict(class_type='KSampler',_meta=dict(title='KSampler '+ident),inputs=dict(
            seed=10,steps=20,cfg=8.0,sampler_name='euler',scheduler='normal',denoise=1.0,latent_image=['5',0]))
    return profile


def inspection(profile):
    return dict(format='prompt_studio_native_inspection',version=1,epoch=0,
        identity=dict(frontend_id=profile['frontend_id'],path=profile['origin']['path']),
        workflow=dict(id=profile['frontend_id'],nodes=[]),output=copy.deepcopy(profile['graph']),parameters=profile_fields(profile))
