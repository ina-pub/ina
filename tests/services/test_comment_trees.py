from __future__ import annotations

from app.repos.base import TreeRow
from app.services.comments import LINK, More, select, sort_tree

#  a(10)          b(50)        c(1)
#  ├─ a1(5)       └─ b1(2)
#  │  └─ a1x(9)      └─ b1x(1)
#  └─ a2(7)             └─ b1xy(1)
IDS = {'a': 1, 'b': 2, 'c': 3, 'a1': 4, 'a2': 5, 'a1x': 6, 'b1': 7, 'b1x': 8, 'b1xy': 9}
PARENTS = {'a1': 'a', 'a2': 'a', 'a1x': 'a1', 'b1': 'b', 'b1x': 'b1', 'b1xy': 'b1x'}
SCORES = {'a': 10, 'b': 50, 'c': 1, 'a1': 5, 'a2': 7, 'a1x': 9, 'b1': 2, 'b1x': 1, 'b1xy': 1}
NAMES = {number: name for name, number in IDS.items()}
OP = 99


def rows() -> list[TreeRow]:
    return [
        TreeRow(
            IDS[name],
            IDS[PARENTS[name]] if name in PARENTS else None,
            SCORES[name],
            SCORES[name] / 100,
            0.0,
            float(IDS[name]),
            OP if name == 'b1' else 1,
            None,
        )
        for name in IDS
    ]


def names(ids: list[int] | tuple[int, ...]) -> list[str]:
    return [NAMES[i] for i in ids]


def test_orders() -> None:
    assert names(sort_tree(rows(), 'top').children[LINK]) == ['b', 'a', 'c']
    assert names(sort_tree(rows(), 'new').children[LINK]) == ['c', 'b', 'a']
    assert names(sort_tree(rows(), 'old').children[IDS['a']]) == ['a1', 'a2']
    assert names(sort_tree(rows(), 'confidence').order[:3]) == ['b', 'a', 'a1x']


def test_everything_fits() -> None:
    chosen = select(sort_tree(rows(), 'top'), limit=100)
    assert chosen.more is None
    b, a, c = chosen.roots
    assert names([b.id, a.id, c.id]) == ['b', 'a', 'c']
    assert names([kid.id for kid in a.children]) == ['a2', 'a1']
    assert names([b.children[0].children[0].children[0].id]) == ['b1xy']
    assert sorted(names(chosen.ids())) == sorted(IDS)


def test_limit_takes_the_best_across_depths() -> None:
    chosen = select(sort_tree(rows(), 'top'), limit=4)
    # b(50) and a(10), then a's replies a2(7) and a1(5) beat b1(2) and c(1); a1x(9) only
    # becomes a candidate once a1 is taken, and by then the limit is reached.
    assert sorted(names(chosen.ids())) == ['a', 'a1', 'a2', 'b']
    assert chosen.more == More(parent_id=None, depth=0, ids=(IDS['c'],), count=1)
    b, a = chosen.roots
    assert b.more == More(parent_id=IDS['b'], depth=1, ids=(IDS['b1'],), count=3)
    _, a1 = a.children
    assert a1.more == More(parent_id=IDS['a1'], depth=2, ids=(IDS['a1x'],), count=1)


def test_depth_limit_continues_the_thread() -> None:
    chosen = select(sort_tree(rows(), 'top'), limit=100, max_depth=2)
    b = chosen.roots[0]
    (b1,) = b.children
    assert b1.children == ()
    assert b1.continued is True
    assert b1.more is None


def test_starts_and_sticky() -> None:
    tree = sort_tree(rows(), 'top')
    sub = select(tree, starts=[IDS['a1']], limit=10)
    assert names([root.id for root in sub.roots]) == ['a1']
    assert names([kid.id for kid in sub.roots[0].children]) == ['a1x']
    stickied = select(tree, limit=1, first=IDS['c'])
    assert names([root.id for root in stickied.roots]) == ['c']


def test_qa_keeps_answers_and_their_questions() -> None:
    tree = sort_tree(rows(), 'qa', answerers={OP})
    kept = set(names(tree.order))
    # Top level, the OP's b1 and the reply to it, b1x; b1xy answers nobody.
    assert kept == {'a', 'b', 'c', 'b1', 'b1x'}
