from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from astra_luna import transport


class TransportTests(unittest.TestCase):
    def test_metadata_drops_content_and_preserves_native_lineage(self):
        result = transport.metadata({'id':'child','parentThreadId':'root','agentRole':'adaptive_luna_max',
            'preview':'must not persist','turns':['must not persist'],'status':{'type':'idle','extra':'private'},
            'source':{'subagent':{'thread_spawn':{'parent_thread_id':'root','agent_role':'adaptive_luna_max','depth':1,'prompt':'private'}}}})
        self.assertNotIn('preview', result)
        self.assertNotIn('turns', result)
        self.assertEqual(result['spawn'],dict(parent_thread_id='root',agent_role='adaptive_luna_max',depth=1))
        self.assertEqual(result['status'], {'type':'idle'})

    def test_stream_only_keeps_lifecycle(self):
        s = object.__new__(transport.Session)
        s.root='root';s.children=set();s.events=[];s.completed=False;s.started=0
        s.handle({'method':'item/agentMessage/delta','params':{'delta':'do not persist'}})
        self.assertEqual(s.events, [])
        s.handle({'method':'item/started','params':{'threadId':'root','item':{
            'id':'call','type':'subAgentActivity','agentThreadId':'child','text':'do not persist'}}})
        self.assertEqual(s.children, {'child'})
        self.assertNotIn('text', s.events[0]['item'])
        s.handle({'method':'turn/completed','params':{'threadId':'root','turn':{'id':'turn','status':'completed','items':['private']}}})
        self.assertTrue(s.completed)
        self.assertNotIn('items', s.events[-1]['turn'])

    def test_server_approval_request_is_rejected(self):
        s=object.__new__(transport.Session)
        sent=[]
        s.send=sent.append
        with self.assertRaisesRegex(RuntimeError,'approval'):
            s.handle({'id':1,'method':'item/commandExecution/requestApproval','params':{}})
        self.assertIn('error',sent[0])

    def test_scope_extra_file_or_links_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            for name in ('AGENTS.md','contract.md','normalize_tags.py','unique_numbers.py'):
                (root/name).write_text('fixture',encoding='utf-8')
            self.assertEqual(len(transport.hashes(root)),4)
            (root/'extra.py').write_text('unexpected',encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'scope'):
                transport.hashes(root)

    def test_invalid_run_never_starts_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            with patch.object(transport,'Session',side_effect=AssertionError('unexpected model call')):
                result=transport.run(root,'codex',root/'project',root/'project'/'evidence','adaptive_luna_max')
            self.assertEqual(result['status'],'LIVE_FAILED')
            self.assertFalse((root/'project').exists())


if __name__=='__main__':
    unittest.main()
