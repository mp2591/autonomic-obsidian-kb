from __future__ import annotations
import tempfile,unittest
from pathlib import Path
from autonomic_kb.index import KnowledgeIndex
from tests.support import make_vault,write_memory
class IndexTests(unittest.TestCase):
 def test_index_search_update_and_delete(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary); config=make_vault(root); path=write_memory(config,'commands/test.md','kb:repository:command:test','Test command','Run the deterministic unittest suite',memory_type='command')
   with KnowledgeIndex(config) as index:
    stats=index.index_vault(); self.assertEqual(stats.indexed,1); hits=index.search('deterministic unittest',10); self.assertEqual(hits[0]['declared_id'],'kb:repository:command:test'); original_hash=index.get('kb:repository:command:test')['source_hash']; path.write_text(path.read_text().replace('deterministic','complete')); update=index.index_vault(); self.assertEqual(update.updated,1); self.assertNotEqual(index.get('kb:repository:command:test')['source_hash'],original_hash); path.unlink(); deleted=index.index_vault(); self.assertEqual(deleted.deleted,1)
 def test_relationships_are_indexed(self):
  with tempfile.TemporaryDirectory() as temporary:
   config=make_vault(Path(temporary)); write_memory(config,'fail.md','kb:repository:known-failure:lock','Lock failure','database locked',memory_type='known-failure',relations={'fixed-by':['kb:repository:solution:close']}); write_memory(config,'fix.md','kb:repository:solution:close','Close connection','close the sqlite connection',memory_type='solution')
   with KnowledgeIndex(config) as index:
    index.index_vault(); neighbors=index.neighbors(['kb:repository:known-failure:lock']); self.assertEqual(neighbors[0]['declared_id'],'kb:repository:solution:close'); self.assertEqual(neighbors[0]['graph_relation'],'fixed-by')
if __name__=='__main__':unittest.main()
