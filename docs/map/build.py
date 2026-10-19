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
FILES = ['autodealer.py', 'card.py', 'players/player_simple.py', 'players/player_random.py', 'player.py', 'dealer.py']
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
    order = [card_mod.Card(c[0], c[1:]) for c in route['cards']]
    filler = [card_mod.Card('♣', '2') for _ in range(30)]
    ad.deck = filler + order[::-1]
    ad.round = 1
    bets = list(route.get('bets', []))

    class P(simple.BlackjackPlayer):
        calls = 0

        def place_bet(self, balance):
            P.calls += 1
            if P.calls > 1:
                return 0          # 2ラウンド目は遊ばない（1周だけ見る）
            if bets:
                return bets[0]    # ルートで決めた掛け金（テスト用）
            return super().place_bet(balance)

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
                if sid == 's1' and any(p['id'] == 's1' for p in path):
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
    return {'path': path, 'calls': calls, 'result': list(result), 'edges': edges}


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
    print(f"  dealer.py 種 {dealer['seed']}: 呼び出し {len(dealer['edges'])} 種類")
    data = {'ring': ring, 'sources': src, 'traces': traces, 'roles': roles, 'roleNames': ROLE_NAMES, 'dealer': dealer, 'diff': dealer_diff()}
    blob = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    page = (HERE / 'ring.src.html').read_text(encoding='utf-8')
    assert page.count('/*__DATA__*/{}') == 1, '埋め込む印 /*__DATA__*/{} が1つだけあるはず'
    out = HERE / 'ring.html'
    out.write_text(page.replace('/*__DATA__*/{}', blob), encoding='utf-8')
    return out


if __name__ == '__main__':
    print('作成:', build())
