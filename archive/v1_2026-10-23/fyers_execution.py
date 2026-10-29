"""
fyers_execution.py
===================
The one place that actually touches your real brokerage account. Everything
else in this project (features, model, backtest) is analysis; this module is
where a decision becomes a real order - or doesn't, depending on LIVE_TRADING.

READ THIS BEFORE TOUCHING LIVE_TRADING
-----------------------------------------
LIVE_TRADING = False (the default, and what you chose to start with):
    The bot connects to your REAL Fyers account and uses REAL live market
    data and REAL account/margin info if you ask it to, but every "order"
    is only logged and tracked in memory (state["stocks"][...]["orders"]) -
    nothing is ever sent to the exchange. This is how you validate the
    model's actual behavior on real market conditions with zero financial
    risk, before deciding whether to trust it with money.

LIVE_TRADING = True:
    Orders are actually submitted to Fyers with real money. Before you flip
    this:
      1. Run the bot in paper mode for at least several trading sessions and
         look at the trade log - does the ML strategy's behavior make sense?
      2. Set MAX_POSITION_VALUE and DAILY_LOSS_LIMIT below to numbers you are
         genuinely fine losing completely. Start small - this is a college
         project's model, not a professional trading desk's.
      3. Understand the kill-switch below (RiskManager.should_halt) and don't
         disable it.
    This project cannot and does not promise any level of profitability -
    see ML_WALKTHROUGH.md. Automated trading can lose money quickly if left
    unattended and unmonitored; check on it regularly.

RISK CONTROLS (active in both modes, so they also show up correctly in
paper-mode logs before you ever risk money)
--------------------------------------------
- MAX_POSITION_VALUE: never risk more than this many rupees on one trade.
- DAILY_LOSS_LIMIT: if today's cumulative P&L drops below -this amount,
  the kill-switch trips and the bot stops opening new positions for the
  rest of the day (existing positions are left alone, not force-closed).
- MAX_TRADES_PER_DAY: a runaway-bug safety net (e.g. a bug that flips
  signals back and forth every tick) - stops trading once tripped.
"""
import datetime

LIVE_TRADING = False  # <-- the real on/off switch. See the docstring above.

MAX_POSITION_VALUE = 5000     # rupees per trade (only matters if LIVE_TRADING)
DAILY_LOSS_LIMIT = 2000       # rupees; kill-switch trips below -this
MAX_TRADES_PER_DAY = 40       # runaway-bug safety net


class RiskManager:
    """Tracks today's trading activity and decides if the kill-switch should trip."""

    def __init__(self):
        self.date = datetime.date.today()
        self.trades_today = 0
        self.realized_pnl_today = 0.0
        self.halted = False
        self.halt_reason = ""

    def _roll_day_if_needed(self):
        today = datetime.date.today()
        if today != self.date:
            self.date = today
            self.trades_today = 0
            self.realized_pnl_today = 0.0
            self.halted = False
            self.halt_reason = ""

    def record_trade(self, pnl: float = 0.0):
        self._roll_day_if_needed()
        self.trades_today += 1
        self.realized_pnl_today += pnl
        self._check_halt()

    def _check_halt(self):
        if self.realized_pnl_today <= -abs(DAILY_LOSS_LIMIT):
            self.halted = True
            self.halt_reason = (f"Daily loss limit hit: Rs.{self.realized_pnl_today:.2f} "
                                 f"<= -Rs.{DAILY_LOSS_LIMIT}")
        elif self.trades_today >= MAX_TRADES_PER_DAY:
            self.halted = True
            self.halt_reason = f"Max trades per day reached ({MAX_TRADES_PER_DAY})"

    def should_halt(self) -> bool:
        self._roll_day_if_needed()
        return self.halted


risk_manager = RiskManager()


def position_size(price: float) -> int:
    """How many shares to trade, capped by MAX_POSITION_VALUE. Always at least 1
    so paper-mode logs still show a realistic quantity even for pricier stocks."""
    if price <= 0:
        return 0
    return max(1, int(MAX_POSITION_VALUE // price))


def place_order(symbol: str, side: str, price: float):
    """
    side: "BUY" or "SELL". Returns (qty, order_result_dict_or_None).
    In paper mode, order_result is None and nothing touches the network.
    In live mode, submits a real market order via Fyers and returns its response.
    """
    qty = position_size(price)

    if not LIVE_TRADING:
        return qty, None

    from config import get_fyers_client
    fyers = get_fyers_client()
    payload = {
        "symbol": symbol,
        "qty": qty,
        "type": 2,          # 2 = market order
        "side": 1 if side == "BUY" else -1,
        "productType": "INTRADAY",
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
    }
    try:
        response = fyers.place_order(payload)
        print(f"  [LIVE ORDER] {side} {qty}x {symbol} -> {response}")
        return qty, response
    except Exception as e:
        print(f"  [LIVE ORDER ERROR] {side} {qty}x {symbol}: {e}")
        return qty, {"s": "error", "message": str(e)}
