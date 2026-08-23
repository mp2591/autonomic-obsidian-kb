from pathlib import Path
from typing import Any
from autonomic_kb.config import KBConfig
from autonomic_kb.markdown import render_note
from autonomic_kb.util import utc_now

def make_vault(root: Path, repo: Path|None=None)->KBConfig:
    vault=root/'vault'; vault.mkdir(parents=True,exist_ok=True)
    (vault/'kb.toml').write_text('[retrieval]\ndefault_budget=500\nminimum_score=0.20\nmax_candidates=100\n[lifecycle]\npromotion_threshold=0.62\nstale_after_days=120\narchive_after_days=365\n[security]\nallow_untrusted=false\nallow_cross_repo=false\n[paths]\ninbox="00-inbox"\narchive="99-archive"\nquarantine="98-quarantine"\n',encoding='utf-8')
    return KBConfig.load(vault,repo)

def write_memory(config:KBConfig,relative:str,memory_id:str,title:str,summary:str,memory_type='fact',scope='repository',status='active',confidence=.9,authority='verified',applies_to=None,relations=None,**metadata:Any)->Path:
    now=utc_now(); base={'id':memory_id,'title':title,'type':memory_type,'scope':scope,'status':status,'summary':summary,'confidence':confidence,'authority':authority,'created':now,'updated':now,'validated':now,'freshness':'verified','token_cost':max(40,len(summary)//2),'utility':.8,'applies_to':applies_to or [],'provenance':[{'kind':'test'}],'relations':relations or {},'invalidation':{}}
    base.update(metadata); layers={0:summary[:100],1:summary,2:summary+' Additional summary context for the common path.',3:summary+' Full detail, rationale, edge cases, and implementation context. '*4}
    path=config.vault/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(render_note(base,layers),encoding='utf-8'); return path
