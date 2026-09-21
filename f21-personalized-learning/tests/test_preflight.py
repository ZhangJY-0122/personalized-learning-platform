import unittest,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from preflight import update,replay,topological
class PreflightTest(unittest.TestCase):
 def test_exact_rational_vectors(self):
  for v in json.loads((ROOT/"tests/fixtures/bkt-vectors.json").read_text()):
   self.assertAlmostEqual(update(v["before"],v["correct"],v["weight"]),v["expected"],places=12)
 def test_order_and_duplicate(self):
  events=json.loads((ROOT/"data/demo/scenario.json").read_text())["events"]
  self.assertEqual(replay(events),replay(events*10))
  self.assertEqual(replay(events),replay(events[::-1]))
 def test_same_id_changed_content_rejected(self):
  e=json.loads((ROOT/"data/demo/scenario.json").read_text())["events"][0]
  with self.assertRaises(ValueError): replay([e,{**e,"correct":True}])
 def test_non_answer_cannot_raise_mastery(self):
  self.assertEqual(replay([{"eventId":"1","eventSeq":1,"occurredAt":"t","eventType":"RESOURCE_COMPLETED"}])[0],.2)
 def test_cycle_rejected(self):
  with self.assertRaises(ValueError): topological(["a","b"],[["a","b"],["b","a"]])
 def test_missing_prerequisite_rejected(self):
  with self.assertRaises(ValueError): topological(["a"],[["z","a"]])
 def test_path_order(self):
  self.assertEqual(topological(["c","a","b"],[["a","b"],["b","c"]]),["a","b","c"])
if __name__=="__main__": unittest.main()
