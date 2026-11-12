"""检验研究合同和统计反例，不镜像实现。"""
import unittest

import numpy as np
import pandas as pd

from inference import bh_adjust, paired_inference, family_test
from state_machine import LifecycleParams, replay_lifecycle


class InferenceTests(unittest.TestCase):
    def test_uniform_improvement_detected(self):
        a=pd.DataFrame({'cycle':range(20),'net':[.01]*20})
        b=a.assign(net=0.)
        result=paired_inference(a,b)
        self.assertLess(result['bootstrap_p_two_sided'], .001)
        self.assertTrue(np.allclose(result['mean_cycle_delta_ci95'], [.01,.01]))

    def test_identical_paths_do_not_reject(self):
        a=pd.DataFrame({'cycle':range(20),'net':np.sin(np.arange(20))*.01})
        result=paired_inference(a,a)
        self.assertEqual(result['bootstrap_p_two_sided'],1.)
        self.assertEqual(result['terminal_delta'],0.)

    def test_pair_misalignment_rejected(self):
        a=pd.DataFrame({'cycle':range(20),'net':[.01]*20})
        with self.assertRaises(ValueError):paired_inference(a,a.iloc[:-1])

    def test_bh_known_example(self):
        np.testing.assert_allclose(bh_adjust([.01,.04,.03]),[.03,.04,.04])

    def test_max_test_never_claims_positive_for_negative_constants(self):
        result=family_test(np.full((22,6),-.01))
        self.assertEqual(result['global_p_positive'],1.)
        self.assertEqual(result['p_max_positive'],[1.]*6)


class StateContractTests(unittest.TestCase):
    def source(self, monitors):
        return pd.DataFrame({'date':[f'{x:04}' for x in range(len(monitors))], 'monitor':monitors})

    def test_future_returns_never_enter_state(self):
        frame=self.source([1.,0.,0.,0.,1.,1.,1.,1.])
        other=frame.assign(realized_return=[100.,-100.]*4)
        pd.testing.assert_frame_equal(replay_lifecycle(frame,None,LifecycleParams(.5)),
                                      replay_lifecycle(other,None,LifecycleParams(.5)))

    def test_missing_observations_cannot_restore_retired_state(self):
        frame=self.source([0.,0.,0.,np.nan,np.nan,np.nan])
        ledger=replay_lifecycle(frame,None,LifecycleParams(.5))
        self.assertEqual(ledger.iloc[-1].state_after,'RETIRED')
        self.assertEqual(ledger.iloc[-1].good_streak,0)

    def test_out_of_order_decision_dates_rejected(self):
        frame=self.source([1.,0.,0.])
        with self.assertRaises(ValueError):replay_lifecycle(frame,['0001','0000'],LifecycleParams(.5))

    def test_duplicate_monitor_rejected(self):
        frame=self.source([1.,0.]); frame['date']='same'
        with self.assertRaises(ValueError):replay_lifecycle(frame,None,LifecycleParams(.5))


if __name__=='__main__':unittest.main()
