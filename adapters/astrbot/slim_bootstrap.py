"""Version-checked lazy imports for hosted AstrBot (upstream dad255c).

No SDK stub, sys.modules spoofing, disabled capability or source-tree mutation.
Provider SDKs load when selected; 3rd-party runners load on their actual branch.
The original framework still owns Persona, DB, message pipeline and model calls.
Call install() before importing astrbot.core.core_lifecycle.
"""
import ast
import importlib.abc
import importlib.machinery
import sys

TARGETS={
    'astrbot.core.provider.entities':{
        'anthropic.types','google.genai.types','openai.types.chat.chat_completion','openai.types.responses',
    },
    'astrbot.core.pipeline.process_stage.method.agent_sub_stages.third_party':{
        'astrbot.core.agent.runners.coze.coze_agent_runner',
        'astrbot.core.agent.runners.dashscope.dashscope_agent_runner',
        'astrbot.core.agent.runners.deerflow.deerflow_agent_runner',
        'astrbot.core.agent.runners.dify.dify_agent_runner',
    },
}

def transform(name,source):
    tree=ast.parse(source)
    targets=TARGETS[name];removed=[]
    for node in list(tree.body):
        if isinstance(node,ast.ImportFrom) and node.module in targets:
            removed.append(node);tree.body.remove(node)
    if {n.module for n in removed}!=targets:
        raise RuntimeError('AstrBot slim patch no longer matches pinned source: '+name)
    if name.endswith('.entities'):
        # These names only occur in postponed annotations in pinned upstream.
        aliases={n.asname or n.name for entry in removed for n in entry.names}
        for node in ast.walk(tree):
            if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load) and node.id in aliases:
                # Ensure every runtime AST occurrence belongs to an annotation.
                parents=[n for n in ast.walk(tree) if isinstance(n,(ast.AnnAssign,ast.arg)) and n.annotation is not None and node in ast.walk(n.annotation)]
                # Function returns are not among current uses; source changes fail.
                if not parents:raise RuntimeError('SDK type now used at runtime: '+node.id)
        check=ast.parse('from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass')
        check.body[1].body=removed
        insert=1 if isinstance(tree.body[0],ast.ImportFrom) and tree.body[0].module=='__future__' else 0
        tree.body[insert:insert]=check.body
        mapping={entry.names[0].asname or entry.names[0].name:(entry.module,entry.names[0].name) for entry in removed}
        if any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='__getattr__' for n in tree.body):
            raise RuntimeError('SDK module attribute hook changed upstream')
        tree.body.extend(ast.parse(f'''
def __getattr__(name):
    imports={mapping!r}
    if name not in imports:raise AttributeError(name)
    import importlib
    module,attribute=imports[name]
    value=getattr(importlib.import_module(module),attribute)
    globals()[name]=value
    return value
''').body)
    else:
        for entry in removed:
            alias=entry.names[0].asname or entry.names[0].name
            if sum(isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load) and n.id==alias for n in ast.walk(tree))!=1:
                raise RuntimeError('Runner now referenced outside its selection branch: '+alias)
            matched=0
            for node in ast.walk(tree):
                if not isinstance(node,ast.If):continue
                for statement in node.body:
                    if isinstance(statement,ast.Assign) and any(isinstance(x,ast.Name) and x.id==alias for x in ast.walk(statement.value)):
                        node.body.insert(0,entry);matched+=1;break
            if matched!=1:raise RuntimeError('Runner construction changed: '+alias)
    return ast.fix_missing_locations(tree)

class Loader(importlib.machinery.SourceFileLoader):
    def get_code(self,fullname):
        return compile(transform(fullname,self.get_source(fullname)),self.path,'exec')

class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname not in TARGETS:return None
        spec=importlib.machinery.PathFinder.find_spec(fullname,path)
        if spec is None or not spec.origin:raise ImportError('Pinned AstrBot source is missing')
        spec.loader=Loader(fullname,spec.origin)
        return spec

def install():
    if any(isinstance(finder,Finder) for finder in sys.meta_path):return
    if any(name in sys.modules for name in TARGETS):
        raise RuntimeError('Install slim imports before loading AstrBot lifecycle/entities')
    sys.meta_path.insert(0,Finder())
