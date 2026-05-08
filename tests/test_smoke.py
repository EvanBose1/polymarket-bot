import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bot import Config, Portfolio


def make_cfg(**kw):
    return Config(pk="0x0", funder="0x0", **kw)


def test_portfolio_caps_single_position():
    cfg = make_cfg(max_position_usd=25, max_total_exposure_usd=200)
    pf = Portfolio(cfg)
    assert pf.can_open(25)
    assert not pf.can_open(26)


def test_portfolio_caps_total():
    cfg = make_cfg(max_position_usd=25, max_total_exposure_usd=50)
    pf = Portfolio(cfg)
    pf.book("a", 25); pf.book("b", 25)
    assert not pf.can_open(1)


def test_dry_run_default():
    cfg = make_cfg()
    assert cfg.dry_run is True
