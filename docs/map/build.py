"""ブラックジャックの環状線と部品図のページを作る。

    python docs/map/build.py

ring.json（駅・ルート・旗）を読み、ルートごとに本物の autodealer.game() を、カードの順番を決めた山札で1ラウンド流す。
そのとき実行した行（autodealer.py・card.py・players/player_simple.py）を sys.settrace で記録し、駅ごとに分ける。
駅は ring.json の begin の行に着いたら切り替わる。駅に着いたときの game() の変数（手札・掛け金・残高）を「荷物」として残す。
ring.src.html の /*__DATA__*/{} にそれを埋め込んで ring.html を作る（ring.html はコミットしない）。
コードを変えて行がずれたら、build.py が止まるので ring.json の begin と anchor を直す。
"""
import importlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
# players/ にあるプレイヤーは、今のフォルダの中身を毎回探して全部入れる（ファイルを足したら build.py を流し直す）
PLAYERS = sorted(f'players/{p}' for p in __import__('os').listdir(REPO / 'players') if p.endswith('.py'))
FILES = ['autodealer.py', 'card.py', 'player.py', 'dealer.py'] + PLAYERS
sys.path.insert(0, str(REPO))

# 部品図の列（役割）。上から順に当てはめ、最初に当たったものにする
ROLES = [
    ('定義', r'^\s*(def |class |@)'),
    ('表示', r'^\s*(printx?|print)\('),
    ('入力', r'\binput\(|place_bet\('),
    ('判定', r'^\s*(if |elif |else\s*:|while |for |try\s*:|except)'),
    ('流れ', r'^\s*(return|break|continue|pass)\b'),
    ('データ', r'deck|\.append\(|\.pop\(|\.copy\(|hand\s*=|\bCard\(|\[.* for .* in '),
    ('つなぐ', r'^\s*player\.|^\s*\w+\(\)\s*(#|$)'),
    ('計算', r'\+=|-=|sum\(|\*'),
    ('状態', r'^\s*(global |self\.\w+\s*=|\w+\s*=)'),
]
ROLE_NAMES = [r for r, _ in ROLES] + ['その他']


def role(text: str) -> str:
    for name, pat in ROLES:
        if re.search(pat, text):
            return name
    return 'その他'


def modname(f: str) -> str:
    """ファイルの名前から、実行時のモジュール名（players/player_simple.py → players.player_simple）"""
    return f[:-3].replace('/', '.')


def run_route(ad, simple, card_mod, route, stations):
    """1つのルートを本物の game() で1ラウンド流し、駅ごとの行と荷物を返す。"""
    begin = {}
    for st in stations:
        for ln in st['begin']:
            begin[ln] = st['id']
    # 山札: pop() は末尾から取るので、配る順を逆にして後ろに置く。前に 30 枚置いて、シャッフルされないようにする
    if 'seed' not in route:
        order = [card_mod.Card(c[0], c[1:]) for c in route['cards']]
        filler = [card_mod.Card('♣', '2') for _ in range(30)]
        ad.deck = filler + order[::-1]
    else:   # カードを決めずに、種 seed でふつうに混ぜた山札（判断の部分の「負けて終わるまで」と同じゲーム）
        import random
        random.seed(route['seed'])
        ad.deck = [card_mod.Card(su, v) for su in ad.suits for v in ad.values]
        random.shuffle(ad.deck)
    ad.round = 1
    bets = list(route.get('bets', []))
    full = route.get('full', False)          # True ならゲームが終わるまで流す（何周も）
    variant = route.get('variant', {})       # place_bet・input を1行だけ変えたプレイヤー

    class P(simple.BlackjackPlayer):
        calls = 0

        def place_bet(self, balance):
            P.calls += 1
            if (P.calls > 1 and not full) or P.calls > 60:
                return 0          # 1周だけ見るときは、2ラウンド目は遊ばない
            if bets:
                return bets[0]    # ルートで決めた掛け金（テスト用）
            if variant.get('place_bet') == 'allin':
                self.balance = balance
                return balance    # 全額を賭ける（place_bet の1行を変えたもの）
            return super().place_bet(balance)

        def input(self, message):
            if variant.get('input') == 'stand':
                return "stand"    # いつも stand（input の中身を1行にしたもの）
            return super().input(message)

    files = {str(REPO / f): f for f in FILES}
    edges = {}
    path, cur, done = [], None, False
    calls = {}
    keep = ('player_hand', 'dealer_hand', 'bet_amount', 'player_balance', 'is_burst', 'turns', 'player_choice')

    def snap(fr):
        out = {}
        for k in keep:
            if k in fr.f_locals:
                v = fr.f_locals[k]
                out[k] = [str(c) for c in v] if isinstance(v, list) else v
        out['deck_left'] = len(ad.deck)
        out['round'] = ad.round
        return out

    def local(fr, ev, arg):
        nonlocal cur, done
        if done:
            return None
        f = files.get(fr.f_code.co_filename)
        if ev == 'line' and f:
            ln = fr.f_lineno
            if f == 'autodealer.py' and fr.f_code.co_name == 'game' and ln in begin:
                sid = begin[ln]
                if sid == 's1' and not full and any(p['id'] == 's1' for p in path):
                    cur['after'] = snap(fr)
                    done = True   # 2周目の始まりで止める
                    return None
                if not cur or cur['id'] != sid:
                    if cur is not None:
                        cur['after'] = snap(fr)   # 前の駅を出るときの荷物
                    cur = {'id': sid, 'lines': {}, 'cargo': snap(fr)}
                    path.append(cur)
            if cur is not None:
                key = f'{f}:{ln}'
                cur['lines'][key] = cur['lines'].get(key, 0) + 1
        return local

    def glob(fr, ev, arg):
        if done:
            return None
        f = files.get(fr.f_code.co_filename)
        if not f:
            return None
        if ev == 'call':   # 実行時の呼び出し: 呼んだ所（リポジトリのファイルの中で一番近い枠）→ 呼ばれたクラス.メソッド
            up = fr.f_back
            while up is not None and up.f_code.co_filename not in files:
                up = up.f_back
            if up is not None:
                key = f"{modname(files[up.f_code.co_filename])}.{up.f_code.co_qualname}:{up.f_lineno} -> {modname(f)}.{fr.f_code.co_qualname}"
                edges[key] = edges.get(key, 0) + 1
        if ev == 'call' and f == 'card.py' and fr.f_code.co_name in ('sum', 'is_soft', 'is_blackjack'):
            name = 'Card.' + fr.f_code.co_name
            calls[name] = calls.get(name, 0) + 1
            if cur is not None:
                cur.setdefault('calls', {})
                cur['calls'][name] = cur['calls'].get(name, 0) + 1
        return local

    # s0（入口）は game() の最初の行。begin に入っていないと path が空で始まるので、最初の駅は s0 になる
    sys.settrace(glob)
    try:
        result = ad.game(P)
    finally:
        sys.settrace(None)
    if route.get('tail'):   # 最後のラウンドだけ残す（何十周もするゲームの、終わりの所だけを見る）
        last = max(i for i, p in enumerate(path) if p['id'] == 's1')
        path = path[last:]
    return {'path': path, 'calls': calls, 'result': list(result), 'edges': edges}


def _show(v):
    """値を短い文字にする（Card は ♠5 のように）"""
    if isinstance(v, list):
        return '[' + ', '.join(_show(x) for x in v) + ']'
    if type(v).__name__ == 'Card':
        return str(v)
    r = repr(v)
    return r if len(r) <= 30 else r[:27] + '…'


def trace_decisions(ad, card_mod, scenarios) -> dict:
    """players/ の今いるプレイヤーごとに、場面（勝つ・負ける・ゲームの最後まで）を本物の autodealer.game() で流し、
    判断の部分（place_bet と input）が通った行を順番に、その行に出てくる変数の値と一緒に記録する。"""
    import random
    import re
    word = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?')
    out = {}
    for f in PLAYERS:
        mod = importlib.import_module(modname(f))
        path = str(REPO / f)
        lines = (REPO / f).read_text(encoding='utf-8').split('\n')
        out[f] = {}
        for sc in scenarios:
            log, cur = [], {'seq': None, 'ret': None}

            def tracer(fr, ev, arg):
                if fr.f_code.co_filename != path:
                    return None
                if ev == 'line' and cur['seq'] is not None:
                    ln = fr.f_lineno
                    vals = {}
                    for w in word.findall(lines[ln - 1].split('#')[0]):
                        if w.startswith('self.'):
                            a = w[5:]
                            if a in fr.f_locals['self'].__dict__:
                                vals[w] = _show(getattr(fr.f_locals['self'], a))
                        elif w in fr.f_locals and w != 'self':
                            vals[w] = _show(fr.f_locals[w])
                    cur['seq'].append([ln, vals])
                if ev == 'return' and cur['seq'] is not None and fr.f_code.co_name in ('place_bet', 'input'):
                    cur['ret'] = _show(arg)
                return tracer

            class P(mod.BlackjackPlayer):
                calls = 0

                def place_bet(self, balance):
                    P.calls += 1
                    if P.calls > (1 if sc['rounds'] == 1 else 40):
                        return 0
                    cur['seq'] = []
                    sys.settrace(tracer)
                    try:
                        r = super().place_bet(balance)
                    finally:
                        sys.settrace(None)
                    log.append({'m': 'place_bet', 'round': P.calls, 'balance': balance, 'ret': r, 'seq': cur['seq']})
                    cur['seq'] = None
                    return r

                def input(self, message):
                    hand = [str(c) for c in self.hand]
                    total = card_mod.Card.sum(self.hand)
                    cur['seq'] = []
                    sys.settrace(tracer)
                    try:
                        r = super().input(message)
                    finally:
                        sys.settrace(None)
                    log.append({'m': 'input', 'round': P.calls, 'hand': hand, 'sum': total, 'up': str(self.dealer_card), 'ret': r, 'seq': cur['seq']})
                    cur['seq'] = None
                    return r
            ok = {'win': lambda r: r[0] > 1000, 'loseround': lambda r: r[0] < 1000, 'lose': lambda r: r[0] <= 0}.get(sc.get('find'), lambda r: True)
            for seed in ([sc['seed']] + list(range(1, 2000))):
                log.clear()
                P.calls = 0
                random.seed(seed)
                if sc.get('cards'):
                    order = [card_mod.Card(c[0], c[1:]) for c in sc['cards']]
                    ad.deck = [card_mod.Card('♣', '2') for _ in range(30)] + order[::-1]
                else:
                    ad.deck = [card_mod.Card(su, v) for su in ad.suits for v in ad.values]
                    random.shuffle(ad.deck)
                ad.round = 1
                res = ad.game(P)
                if ok(res):
                    break
            else:
                raise SystemExit(f"{f}: 場面 {sc['id']} になる種が見つからない")
            out[f][sc['id']] = {'log': log, 'result': list(res), 'seed': seed}
    return out


def trace_main(ring2) -> dict:
    """autodealer.py の __main__（テストの集計）を、MAX だけ 2 にして本物どおりに流す（行の数は変えない）。
    players/ の全部のプレイヤーが、1人ずつ MAX 回ずつ game() を呼ぶ。駅は ring2.json の begin の行で切り替える。"""
    import contextlib
    import io
    import os
    import random
    pf = str(REPO / 'autodealer.py')
    src = (REPO / 'autodealer.py').read_text(encoding='utf-8')
    assert 'MAX = 4000' in src, 'autodealer.py に MAX = 4000 が無い'
    code = compile(src.replace('MAX = 4000', 'MAX = 2   ', 1), pf, 'exec')
    begin = {ln: st['id'] for st in ring2['stations'] for ln in st['begin']}
    keep = ('file_names_without_extension', 'module_name', 'game_round', 'MAX', 'result', 'num_turn', 'total', 'total_turns')
    path, cur = [], None

    def snap(g):
        out = {}
        for k in keep:
            if k in g:
                v = g[k]
                out[k] = list(v) if isinstance(v, list) else v
        return out

    def tracer(fr, ev, arg):
        nonlocal cur
        if fr.f_code.co_filename != pf:
            return None
        if ev == 'call' and fr.f_code.co_name == 'game' and cur is not None:
            cur.setdefault('calls', {})
            cur['calls']['game()'] = cur['calls'].get('game()', 0) + 1
            return None
        if fr.f_code.co_name != '<module>':
            return None
        if ev == 'line':
            ln = fr.f_lineno
            if ln in begin and (cur is None or cur['id'] != begin[ln]):
                if cur is not None:
                    cur['after'] = snap(fr.f_globals)
                cur = {'id': begin[ln], 'lines': {}, 'cargo': snap(fr.f_globals)}
                path.append(cur)
            if cur is not None:
                key = f'autodealer.py:{ln}'
                cur['lines'][key] = cur['lines'].get(key, 0) + 1
        return tracer
    g = {'__name__': '__main__', '__file__': pf}
    out, here = io.StringIO(), os.getcwd()
    random.seed(7)
    os.chdir(REPO)
    try:
        with contextlib.redirect_stdout(out):
            sys.settrace(tracer)
            try:
                exec(code, g)
            finally:
                sys.settrace(None)
    finally:
        os.chdir(here)
    if cur is not None:
        cur['after'] = snap(g)
    path.append({'id': 'end', 'lines': {}, 'cargo': snap(g)})
    return {'path': path, 'calls': {}, 'result': [0, 0], 'stdout': out.getvalue()}


class _Stop(Exception):
    pass


ENDING_VARS = {
    '<module>': ('module_name', 'game_round', 'MAX', 'result', 'num_turn', 'total', 'total_turns'),
    'game': ('initial_balance', 'player_balance', 'bet_amount', 'turns', 'is_burst', 'player_choice', 'player_hand', 'dealer_hand'),
    'place_bet': ('balance', 'bet_amount'),
    '__init__': ('balance', 'stop_balance', 'unit_bet', 'hit_threshold'),
}
_COND = re.compile(r'^\s*(?:if|elif|while)\s+(.*?):\s*(?:#.*)?$')
_SUB = re.compile(r'Card\.sum\(\w+\)|self\.\w+|len\(deck\)|\bround\b|\w+/MAX')


def _typed(v):
    return [_show(v), type(v).__name__]


def trace_ending(kind: str) -> dict:
    """autodealer.py の __main__ を本物どおりに（MAX = 4000 のまま）流し、最初のゲームが「勝ち逃げ」か「残高 0」で終わる
    乱数の種を探して、そのゲームの最後の2ラウンド → __main__ が受け取る所 → 次のゲームの最初の place_bet →
    MAX 回のあと（結果の1行）までの行と変数の値・型を記録する。if の行は条件を本当に評価して真偽を残す。"""
    import contextlib
    import io
    import os
    import random
    pf = str(REPO / 'autodealer.py')
    pp = str(REPO / 'players' / 'player_simple.py')
    src = (REPO / 'autodealer.py').read_text(encoding='utf-8')
    want = {'win': 63, 'lose': 182}[kind]
    lines = {pf: src.split('\n'), pp: (REPO / 'players' / 'player_simple.py').read_text(encoding='utf-8').split('\n')}

    def run(code, tracer, seed):
        g = {'__name__': '__main__', '__file__': pf}
        here = os.getcwd()
        random.seed(seed)
        os.chdir(REPO)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                sys.settrace(tracer)
                try:
                    exec(code, g)
                except _Stop:
                    pass
                finally:
                    sys.settrace(None)
        finally:
            os.chdir(here)

    # 種を探す: 最初のゲームがどの return で終わるか（MAX を 1 にして最初のゲームだけ）
    quick = compile(src.replace('MAX = 4000', 'MAX = 1   ', 1), pf, 'exec')
    seed = None
    for s in range(200):
        last = {}

        def qt(fr, ev, arg):
            if fr.f_code.co_filename != pf or fr.f_code.co_name != 'game':
                return None
            if ev == 'line':
                last['ln'] = fr.f_lineno
            if ev == 'return':
                last['ret'] = arg
                raise _Stop
            return qt
        run(quick, qt, s)
        if last.get('ln') == want and (kind == 'lose' or last['ret'][0] > 1000):
            seed = s
            break
    assert seed is not None, f'{kind}: 最初のゲームが {want} 行で終わる種が無い'

    code = compile(src, pf, 'exec')
    ev_all, rnd, prev = [], [], []
    st = {'games': 0, 'phase': None, 'done': False, 'final': False}

    def snap(fr):
        keep = ENDING_VARS.get(fr.f_code.co_name, ())
        return {k: _typed(fr.f_locals[k]) for k in keep if k in fr.f_locals}

    def record(fr, ev, arg):
        f = 'autodealer.py' if fr.f_code.co_filename == pf else 'players/player_simple.py'
        ln = fr.f_lineno
        text = lines[fr.f_code.co_filename][ln - 1]
        e = {'f': f, 'ln': ln, 'fn': fr.f_code.co_name, 'vars': snap(fr), 'id': id(fr)}
        if ev == 'return':
            e['ret'] = _typed(arg) if not isinstance(arg, tuple) else [_show(list(arg)).replace('[', '(').replace(']', ')'), 'tuple(' + ', '.join(type(x).__name__ for x in arg) + ')']
        else:
            m = _COND.match(text)
            env = dict(fr.f_globals)
            env.update(fr.f_locals)
            if m:
                try:
                    e['cond'] = bool(eval(m.group(1), env))
                except Exception:
                    pass
            subs = {}
            for x in dict.fromkeys(_SUB.findall(text.split('#')[0])):
                try:
                    subs[x] = _typed(eval(x, env))
                except Exception:
                    pass
            if subs:
                e['subs'] = subs
        return e

    def tracer(fr, ev, arg):
        fn = fr.f_code.co_filename
        if fn != pf and fn != pp:
            return None
        name = fr.f_code.co_name
        if fn == pp and name not in ('place_bet', '__init__') or name in ('printx', 'check_shuffle', '<listcomp>', '<genexpr>'):
            return None
        if ev == 'call':
            if name == 'game':
                st['games'] += 1
                if st['games'] == 2:
                    st['phase'] = 'next'
            return tracer
        if st['done'] and not st['final']:
            if name == '<module>' and ev == 'line' and fr.f_lineno == 213 and fr.f_globals['game_round'] > fr.f_globals['MAX']:
                st['final'] = True
            else:
                return tracer
        if name == '<module>' and ev == 'line' and fr.f_lineno == 206 and st['final']:
            raise _Stop
        if name == '<module>':
            if st['games'] >= 1 and (st['phase'] in ('main', 'next') or st['final']):
                if ev == 'line':
                    e = record(fr, ev, arg)
                    e['phase'] = 'final' if st['final'] else st['phase']
                    ev_all.append(e)
            return tracer
        if st['games'] == 1 and name == 'game' and ev == 'line' and fr.f_lineno == 51:
            prev[:] = rnd
            rnd.clear()
        if st['games'] == 1:
            if ev in ('line', 'return'):
                rnd.append(record(fr, ev, arg))
                if ev == 'return' and name == 'game':
                    for e in prev:
                        e['phase'] = 'prev'
                    for e in rnd:
                        e['phase'] = 'last'
                    ev_all.extend(prev + rnd)
                    st['phase'] = 'main'
        elif st['games'] == 2 and not st['done']:
            if ev in ('line', 'return'):
                e = record(fr, ev, arg)
                e['phase'] = 'next'
                ev_all.append(e)
                if name == 'game' and ev == 'line' and fr.f_lineno == 58:
                    st['done'] = True
        return tracer
    run(code, tracer, seed)
    # 代入で入った値（同じ関数の次の行で見た値）と、次に動いた行
    texts = [lines[pf if e['f'] == 'autodealer.py' else pp][e['ln'] - 1] for e in ev_all]
    for i, e in enumerate(ev_all):
        nxt = next((x for x in ev_all[i + 1:] if x['id'] == e['id']), None)
        if 'ret' in e:
            after = ev_all[i + 1] if i + 1 < len(ev_all) else None
            if after is not None:
                e['nx'] = [after['f'], after['ln'], after['fn']]
            continue
        if nxt is None or (e['fn'] == '<module>' and e['ln'] == 214):
            continue
        e['nx'] = [nxt['f'], nxt['ln'], nxt['fn']]
        m = re.match(r'^\s*([\w, ]+?)\s*(?:\+=|-=|=)(?!=)', texts[i])
        targets = [t.strip() for t in m.group(1).split(',')] if m else []
        ch = {k: v for k, v in nxt['vars'].items() if e['vars'].get(k) != v or k in targets}
        if ch:
            e['set'] = ch
    for e in ev_all:
        del e['id']
    keep = [not t.strip().startswith('printx(') for t in texts]
    ev_all = [e for e, k in zip(ev_all, keep) if k]
    # __main__ の 214 行（game() の戻り値を受け取る）は game() を呼ぶ前に通るので、戻ってきた所に1行足す
    i = next(i for i, e in enumerate(ev_all) if e['phase'] == 'main')
    v = ev_all[i]['vars']
    ev_all.insert(i, {'f': 'autodealer.py', 'ln': 214, 'fn': '<module>', 'phase': 'main', 'vars': {}, 'back': True,
                      'set': {k: v[k] for k in ('result', 'num_turn') if k in v}, 'nx': ['autodealer.py', 215, '<module>']})
    return {'seed': seed, 'kind': kind, 'events': ev_all}


def trace_dealer() -> dict:
    """dealer.py（対話）を、キーボードの代わりに決めた答え（100 → hit → stand → 0）で1ラウンドだけ流す。
    混ぜ方は random.seed で決め、hit しても バーストしない最初の種を使う。実行時の呼び出しと、画面に出た文字を記録する。"""
    import builtins
    import contextlib
    import io
    import random
    import runpy
    files = {str(REPO / f): f for f in FILES}
    for seed in range(500):
        answers = iter(['100', 'hit', 'stand', '0'])
        shown = []

        def fake_input(prompt=''):
            a = next(answers)
            shown.append(f'{prompt}{a}')
            return a
        edges, out = {}, io.StringIO()

        def glob(fr, ev, arg):
            f = files.get(fr.f_code.co_filename)
            if not f:
                return None
            if ev == 'call':
                up = fr.f_back
                while up is not None and up.f_code.co_filename not in files:
                    up = up.f_back
                if up is not None:
                    key = f"{modname(files[up.f_code.co_filename])}.{up.f_code.co_qualname}:{up.f_lineno} -> {modname(f)}.{fr.f_code.co_qualname}"
                    edges[key] = edges.get(key, 0) + 1
            return None
        random.seed(seed)
        real_input, builtins.input = builtins.input, fake_input
        sys.modules.pop('player', None)
        try:
            with contextlib.redirect_stdout(out):
                sys.settrace(glob)
                try:
                    runpy.run_path(str(REPO / 'dealer.py'), run_name='__main__')
                except (SystemExit, StopIteration):
                    pass
                finally:
                    sys.settrace(None)
        finally:
            builtins.input = real_input
        text = out.getvalue()
        if text.count('カードを引きます') == 1 and 'バースト' not in text and '無効' not in text and 'ゲームを終了' in text:
            return {'seed': seed, 'edges': edges, 'stdout': text, 'answers': shown}
    raise SystemExit('dealer.py の1ラウンドを流せる種が見つからない')


def dealer_diff() -> dict:
    """dealer.py と autodealer.py の本物の差分。1ラウンドの流れ（while True から）と、その前の準備（ファイルの上から）に分けて出す。
    autodealer の game() の中は1段深いので、字下げを4つ戻してから比べる。"""
    import difflib
    d = (REPO / 'dealer.py').read_text(encoding='utf-8').split('\n')
    a = (REPO / 'autodealer.py').read_text(encoding='utf-8').split('\n')
    di = d.index('while True:')
    ai = a.index('    while True:')
    ae = next(i for i, ln in enumerate(a) if ln.startswith('if __name__'))
    gi = next(i for i, ln in enumerate(a) if ln.startswith('def game'))
    loop_a = [ln[4:] if ln.startswith('    ') else ln for ln in a[ai:ae]]
    loop = list(difflib.unified_diff(d[di:], loop_a, 'dealer.py', 'autodealer.py の game()', n=0, lineterm=''))
    top = list(difflib.unified_diff(d[:di], a[:ai], 'dealer.py', 'autodealer.py', n=0, lineterm=''))
    return {'loop': loop, 'top': top}


def build() -> pathlib.Path:
    ring = json.loads((HERE / 'ring.json').read_text(encoding='utf-8'))
    src = {f: (REPO / f).read_text(encoding='utf-8') for f in FILES}
    lines = {f: s.split('\n') for f, s in src.items()}
    ids = {s['id'] for s in ring['stations']}
    # 駅の始まりの行が、今のコードと合っているか
    for st in ring['stations']:
        for ln in st['begin']:
            text = lines['autodealer.py'][ln - 1]
            assert text.strip(), f"{st['id']}: autodealer.py:{ln} が空行"
        if st['anchor']:
            text = lines['autodealer.py'][st['begin'][0] - 1]
            assert st['anchor'] in text, f"{st['id']}: autodealer.py:{st['begin'][0]} に「{st['anchor']}」が無い（今は「{text.strip()}」）"
    for it in ring['issues']:
        assert it['station'] in ids, f"{it['id']}: 駅 {it['station']} が無い"

    ad = importlib.import_module('autodealer')
    card_mod = importlib.import_module('card')
    simple = importlib.import_module('players.player_simple')
    traces = {}
    for r in ring['routes']:
        t = run_route(ad, simple, card_mod, r, ring['stations'])
        seen = [p['id'] for p in t['path']]
        for p in t['path']:
            assert p['id'] in ids
        traces[r['id']] = t
        print(f"  {r['id']:8s} {' → '.join(seen)}")

    # 行ごとの役割（部品図の列）
    roles = {}
    for f, ls in lines.items():
        for i, text in enumerate(ls, 1):
            if text.strip() and not text.strip().startswith('#'):
                roles[f'{f}:{i}'] = role(text)
    dealer = trace_dealer()
    cards_of = {r['id']: r['cards'] for r in ring['routes']}
    scenarios = [{'id': 'win', 'title': '勝つ一手', 'cards': cards_of['main'], 'rounds': 1, 'seed': 3, 'find': 'win'},
                 {'id': 'lose', 'title': '負ける一手', 'cards': cards_of['lose'], 'rounds': 1, 'seed': 3, 'find': 'loseround'},
                 {'id': 'game', 'title': '勝ち逃げで終わるまで', 'cards': None, 'rounds': 0, 'seed': 11, 'find': 'win'},
                 {'id': 'gamelose', 'title': '負けて終わるまで', 'cards': None, 'rounds': 0, 'seed': 1, 'find': 'lose'}]
    decisions = trace_decisions(ad, card_mod, scenarios)
    for f, d in decisions.items():
        for k in ('win', 'lose', 'game', 'gamelose'):
            g = d[k]
            print(f"  判断 {f} {k}: 種 {g['seed']}・{sum(1 for x in g['log'] if x['m'] == 'place_bet')} 回の place_bet、結果 {g['result']}")
    ring2 = json.loads((HERE / 'ring2.json').read_text(encoding='utf-8'))
    for st in ring2['stations']:
        for ln in st['begin']:
            text = src['autodealer.py'].split('\n')[ln - 1]
            assert st['anchor'] in text, f"{st['id']}: autodealer.py:{ln} に「{st['anchor']}」が無い（今は「{text.strip()}」）"
    main2 = trace_main(ring2)
    print('  環状線02: ' + ' → '.join(p['id'] for p in main2['path']))
    endings = {k: trace_ending(k) for k in ('win', 'lose')}
    for k, e in endings.items():
        print(f"  終わり方 {k}: 種 {e['seed']}・{len(e['events'])} 行")
    print(f"  dealer.py 種 {dealer['seed']}: 呼び出し {len(dealer['edges'])} 種類")
    data = {'ring': ring, 'sources': src, 'traces': traces, 'roles': roles, 'roleNames': ROLE_NAMES, 'dealer': dealer, 'diff': dealer_diff(), 'players': PLAYERS, 'decisions': decisions, 'endings': endings, 'scenarios': [{k: v for k, v in x.items() if k != 'cards'} for x in scenarios],
            'rings': {'r01': {'ring': ring, 'traces': traces}, 'r02': {'ring': ring2, 'traces': {r['id']: main2 for r in ring2['routes']}}}}
    blob = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    page = (HERE / 'ring.src.html').read_text(encoding='utf-8')
    assert page.count('/*__DATA__*/{}') == 1, '埋め込む印 /*__DATA__*/{} が1つだけあるはず'
    out = HERE / 'ring.html'
    out.write_text(page.replace('/*__DATA__*/{}', blob), encoding='utf-8')
    return out


if __name__ == '__main__':
    print('作成:', build())
