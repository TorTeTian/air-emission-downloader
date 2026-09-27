import unittest
from permit_air import is_jja,air_projection,parse_search,StopRun

class ClientTests(unittest.TestCase):
 def test_periods(self):
  for s in ['2023年06月月报表','2023年7月月报表','2023年08月月报表','2023年第2季度季报表','2023年第3季度季报表','2023年第02季度季报表','2023年第03季度季报表']:
   self.assertTrue(is_jja({'reportTime':s},2023),s)
  for s in ['2023年05月月报表','2023年第1季度季报表','2023年年报表','2024年06月月报表']:
   self.assertFalse(is_jja({'reportTime':s},2023),s)
 def test_projection(self):
  b={'companyInfo':{'permitCode':'x'},'emissionInfo':{'airMainEmission':[{'emitValue':1}],'waterTotalEmission':[{'v':88}]},'monitor':{'airMonitor':[{'v':3}],'waterMonitor':[{'v':7}]},'noise':{'x':2}}
  p=air_projection(b)
  self.assertIn('airMainEmission',p['emissionInfo']);self.assertNotIn('waterTotalEmission',p['emissionInfo']);self.assertNotIn('noise',p)
 def test_bad_page(self):
  with self.assertRaises(StopRun):parse_search('<html>请登录</html>')

if __name__=='__main__':unittest.main(verbosity=2)
