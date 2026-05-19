"""
Tests de sanitat — invariants matematicament obvies que MAI poden fallar.

Si qualsevol d'aquests test falla, hi ha bug i NO podem confiar en cap resultat.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grid_engine import init_grid, process_bar
from trailing_logic import execute_trailing
from fee_model import TRADING_FEE_RATE


def test_cycle_profit_always_positive():
    """Cada cycle complet (BUY+SELL del mateix grid SENSE recolocacio entremig)
    HA de produir profit positiu = step × vol − 2 × fees.
    Si step × vol < 2 × fees, el grid no és viable, però el profit per cycle
    ha de ser un valor concret coneixedor.
    """
    state = init_grid('BTC', 'BTC_USDT', top=82000, bottom=78000, rows=12,
                      capital_quote=200, initial_price=80000)
    expected_step = (82000 - 78000) / 11  # ~$363.64

    # Bar que travessa cell 5 (~$79.818) i cell 6 (~$80.182)
    bar = {'open': 80000, 'high': 80500, 'low': 79500, 'close': 80200, 'volume': 100}
    fills = process_bar(state, bar, ts_ms=0)

    # Trobem el fill SELL amb cycle_profit > 0
    sell_fills = [f for f in fills if f.side == 'SELL']
    assert len(sell_fills) > 0, 'No SELL fillat — bar deuria haver triggerat fillsa'
    for f in sell_fills:
        if f.cycle_profit != 0:  # només els cycles amb buy precedent
            assert f.cycle_profit > 0, (
                f'BUG: cycle_profit NEGATIU = ${f.cycle_profit:.4f}. '
                f'Cell={f.cell_price}, fee={f.fee_quote}'
            )
            print(f'  OK cell @ {f.cell_price:.0f}: cycle_profit=${f.cycle_profit:+.4f}')
    print('PASS: cycle_profit sempre POSITIU')


def test_initial_sells_use_step_not_cost_avg():
    """SELLs inicials no han de usar 'initial_price' com a cost. Han d'usar cell[i-1].
    Així cycle_profit = step × vol − fees (positiu, independent del trend del preu).
    """
    state = init_grid('BTC', 'BTC_USDT', top=82000, bottom=78000, rows=12,
                      capital_quote=200, initial_price=80000)
    # Verifiquem que les open_positions inicials apunten a cell[i-1], NO a initial_price
    for i, side in state.pending.items():
        if side == 'SELL' and i > 0:
            assert i in state.open_positions, f'SELL cell {i} sense open_position'
            expected_cost = state.cells[i - 1]
            actual_cost = state.open_positions[i]
            assert abs(actual_cost - expected_cost) < 0.01, (
                f'BUG: SELL cell {i} té cost {actual_cost} però hauria de ser {expected_cost}'
            )
    print('PASS: SELLs inicials usen cell[i-1] com a cost (step-based)')


def test_recolocation_does_not_carry_cost_avg():
    """Al recolocar, NO portem el cost mig anterior al nou grid.
    Cada nou cycle = step × vol independent del cost històric.
    """
    state = init_grid('BTC', 'BTC_USDT', top=82000, bottom=78000, rows=12,
                      capital_quote=200, initial_price=80000)

    # Recolocació amb preu molt diferent (avall)
    cfg = {'width_pct': 0.0516, 'rows': 12}
    event, reserve_used = execute_trailing(state, cfg, 'near_lower_edge', 78400,
                                             ts_ms=0, reserve_available=100)

    # Després de la recolocació, totes les SELLs noves han d'apuntar a cell[i-1]
    for i, side in state.pending.items():
        if side == 'SELL' and i > 0:
            expected = state.cells[i - 1]
            actual = state.open_positions.get(i)
            assert actual is not None, f'SELL cell {i} sense cost al recolocar'
            assert abs(actual - expected) < 0.01, (
                f'BUG en recoloc: SELL cell {i} té cost {actual} (esperat: {expected})'
            )
    print('PASS: Recolocació no porta cost mig anterior')


def test_sell_in_recoloc_does_not_inflate_grid_alpha():
    """Quan venem inventari en una recolocació amunt, NO sumem al grid_profit_realized.
    Això és MTM, no grid alpha.
    """
    state = init_grid('BTC', 'BTC_USDT', top=82000, bottom=78000, rows=12,
                      capital_quote=200, initial_price=80000)
    gp_before = state.grid_profit_realized
    cfg = {'width_pct': 0.0516, 'rows': 12}
    # Recolocació amunt — hauria de vendre base
    event, _ = execute_trailing(state, cfg, 'near_upper_edge', 81500, ts_ms=0)

    assert event.base_delta <= 0, 'Recoloc amunt hauria de vendre base (delta negatiu)'
    # Grid profit NO ha de canviar per recolocació
    assert abs(state.grid_profit_realized - gp_before) < 0.001, (
        f'BUG: grid_profit ha canviat per recoloc: {gp_before} -> {state.grid_profit_realized}'
    )
    print(f'PASS: Recolocació NO infla grid_alpha (gp: {gp_before:.4f} -> {state.grid_profit_realized:.4f})')


def test_step_vol_vs_fees_threshold():
    """Per cada bot, el step × vol_per_cell ha de superar 2 × fees per cycle.
    Si no, el grid NO és viable (cada cycle perd diners en NET)."""
    for name, top, bottom, rows in [
        ('BTC', 82000, 78000, 12),
        ('ETH', 2400, 2200, 12),
        ('SOL', 100, 90, 9),
        ('PAXG', 4800, 4600, 8),
    ]:
        state = init_grid(name, f'{name}_USDT', top=top, bottom=bottom, rows=rows,
                          capital_quote=200, initial_price=(top+bottom)/2)
        gross = state.step * state.vol_per_cell
        fees = 2 * (top + bottom) / 2 * state.vol_per_cell * TRADING_FEE_RATE
        net = gross - fees
        ratio = gross / fees if fees > 0 else 0
        status = "OK" if net > 0 else "FAIL"
        print(f'  {name}: step=${state.step:.2f} vol={state.vol_per_cell:.6f} '
              f'gross=${gross:.4f} fees=${fees:.4f} net=${net:+.4f} ratio={ratio:.1f}x [{status}]')
        assert net > 0, f'{name}: cycle no rendible (gross < fees)'
    print('PASS: tots els bots tenen cycle profit > fees')


if __name__ == '__main__':
    print('=' * 60)
    print('TESTS DE SANITAT — invariants matematics')
    print('=' * 60)
    print()
    test_cycle_profit_always_positive()
    print()
    test_initial_sells_use_step_not_cost_avg()
    print()
    test_recolocation_does_not_carry_cost_avg()
    print()
    test_sell_in_recoloc_does_not_inflate_grid_alpha()
    print()
    test_step_vol_vs_fees_threshold()
    print()
    print('=' * 60)
    print('TOTS els tests PASSEN')
    print('=' * 60)
