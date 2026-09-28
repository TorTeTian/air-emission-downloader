import unittest
from permit_air import air_projection,parse_search,StopRun
from time_selection import relevant_report,select_time

class ClientTests(unittest.TestCase):
 def test_periods(self):
  selection=select_time(years=[2023],months=[2,10])
  for s in ['2023年02月月报表','2023年第1季度季报表','2023年第04季度季报表','2023年年报表']:
   self.assertTrue(relevant_report({'reportTime':s},selection),s)
  for s in ['2023年05月月报表','2023年第2季度季报表','2024年02月月报表']:
   self.assertFalse(relevant_report({'reportTime':s},selection),s)
 def test_projection(self):
  b={'companyInfo':{'permitCode':'x'},'emissionInfo':{'airMainEmission':[{'emitValue':1}],'waterTotalEmission':[{'v':88}]},'monitor':{'airMonitor':[{'v':3}],'waterMonitor':[{'v':7}]},'noise':{'x':2}}
  p=air_projection(b)
  self.assertIn('airMainEmission',p['emissionInfo']);self.assertNotIn('waterTotalEmission',p['emissionInfo']);self.assertNotIn('noise',p)
 def test_bad_page(self):
  with self.assertRaises(StopRun):parse_search('<html>请登录</html>')

if __name__=='__main__':unittest.main(verbosity=2)
