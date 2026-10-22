from dualtrade.shadow import ShadowLedger, ShadowPosition


def position(style="swing", direction=1):
    return ShadowPosition("AAPL",style,"pullback",direction,100,100,99 if direction==1 else 101,102 if direction==1 else 98,10,1,1,1,10000)


def test_shadow_variants_filter_without_changing_prices():
    s = ShadowLedger()
    s.enter(position(),event_ok=False,sentiment_ok=False)
    assert s.state["technical"]["positions"]
    assert not s.state["events"]["positions"]
    s.mark("AAPL",98,98.1,200)
    # Gapped stop uses the observed executable price, not an imaginary fill at 99.
    assert s.state["technical"]["closed"][0]["pnl"] == -21


def test_slots_and_unlimited_swing_age():
    s = ShadowLedger(); s.enter(position(), True, True)
    s.enter(position("day"), True, True)
    assert len(s.state["technical"]["positions"]) == 1
    s.mark("AAPL",100,100.1,30*86400,macro_flatten=True)
    assert s.state["events"]["positions"]["swing"]


def test_short_uses_ask_and_trailing_never_loosens():
    s=ShadowLedger(); s.enter(position(direction=-1),True,True)
    s.mark("AAPL",98.8,99,200,atr=.2)
    tightened=s.state["technical"]["positions"]["swing"]["stop"]
    s.mark("AAPL",99.1,99.2,205,atr=2)
    assert s.state["technical"]["positions"]["swing"]["stop"] == tightened
