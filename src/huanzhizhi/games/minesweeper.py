"""Minesweeper constraints and risk selection, independent of window appearance.

Boards are flat row-major lists: -1 covered, -2 flag (not proof of a mine),
0..8 revealed clues. Only inferred/verified cells belong in ``mines``.
"""
import math
import random


def neighbors(rows, cols):
    """Return the eight-neighbor graph for a rectangular row-major board."""
    if type(rows) is not int or type(cols) is not int or rows < 1 or cols < 1:
        raise ValueError('棋盘行列数必须是正整数')
    return [{rr * cols + cc
             for rr in range(max(0, r - 1), min(rows, r + 2))
             for cc in range(max(0, c - 1), min(cols, c + 2)) if (rr, cc) != (r, c)}
            for r in range(rows) for c in range(cols)]


def constraints(board,mines,near,total):
    if not board or len(board) != len(near):
        raise ValueError('棋盘与邻格表尺寸不一致或为空')
    if any(type(value) is not int or not -2 <= value <= 8 for value in board):
        raise ValueError('格子必须是 -2、-1 或 0～8 的整数')
    if type(total) is not int or not 0 <= total <= len(board):
        raise ValueError('总雷数超出棋盘范围')
    if any(type(i) is not int or not 0 <= i < len(board) or board[i] >= 0 for i in mines):
        raise ValueError('已知雷位必须是棋盘内尚未展开的格子')
    if any(not isinstance(cells, (set, frozenset)) or i in cells
           or any(type(j) is not int or not 0 <= j < len(board) for j in cells)
           for i, cells in enumerate(near)):
        raise ValueError('邻格表含无效索引或自身')
    unknown={i for i,v in enumerate(board) if v<0}-mines
    eq={}
    for i,v in enumerate(board):
        if v>=0:
            cells=frozenset(near[i]&unknown); n=v-len(near[i]&mines)
            if n<0 or n>len(cells): raise RuntimeError(f'数字与雷位矛盾：格{i}={v},剩余{n},邻格{sorted(cells)},已知雷{sorted(mines)}。')
            if cells:
                if cells in eq and eq[cells]!=n: raise RuntimeError('约束冲突。')
                eq[cells]=n
    cells=frozenset(unknown); remaining=total-len(mines)
    if not 0 <= remaining <= len(cells):
        raise RuntimeError('总雷数与已知雷位或未展开格数矛盾。')
    if cells in eq and eq[cells] != remaining:
        raise RuntimeError('总雷数与数字约束冲突。')
    eq[cells]=remaining
    return unknown,eq

def deduce(board,mines,near,total):
    """Return proven safe cells, proven mines and constraints for choose()."""
    unknown,eq=constraints(board,mines,near,total)
    # Global-count differences help deductions but join unrelated regions in
    # probability search. Keep the original rules for choose() to partition.
    original_eq=eq.copy()
    for _ in range(8):
        safe=set(); bombs=set()
        for cells,n in eq.items():
            if n==0: safe.update(cells)
            elif n==len(cells): bombs.update(cells)
        if safe or bombs: return safe,bombs,original_eq
        added={}
        items=list(eq.items())
        for a,na in items:
            for b,nb in items:
                if a<b:
                    d=b-a; n=nb-na
                    if not 0<=n<=len(d): raise RuntimeError('差分约束冲突。')
                    if d in eq and eq[d] != n or d in added and added[d] != n:
                        raise RuntimeError('差分约束冲突。')
                    if d not in eq: added[d]=n
        if not added: break
        eq.update(added)
        if len(eq)>3000: break
    return set(),set(),original_eq

def choose(board,mines,eq,session,near,total):
    """Return candidate cells and estimated risk; this never performs input.

    Call deduce() to a fixed point first. Search preserves the existing node
    limit and heuristic fallback, so nonzero risk is not a success guarantee.
    The caller decides whether guessing is allowed. ``session`` only needs
    a stop_requested property, checked during bounded enumeration.
    """
    if session.stop_requested:
        raise InterruptedError('用户已停止')
    unknown={i for i,v in enumerate(board) if v<0}-mines
    if not unknown:
        return [],0
    local=[(set(c),n) for c,n in eq.items() if len(c)<len(unknown)]
    frontier=set().union(*(c for c,n in local)) if local else set()
    components=[]; remaining=set(frontier)
    while remaining:
        comp={next(iter(remaining))}
        while True:
            new=comp|set().union(*(c for c,n in local if c&comp))
            if new==comp: break
            comp=new
        remaining-=comp; components.append(comp)
    models=[]
    for comp in components:
        rules=[(c,n) for c,n in local if c&comp]
        order=sorted(comp,key=lambda i:-sum(i in c for c,n in rules))
        hits={i:[j for j,(c,n) in enumerate(rules) if i in c] for i in order}
        needs=[n for c,n in rules]; left=[len(c) for c,n in rules]
        totals={}; counts={i:{} for i in order}; chosen=[]; nodes=0
        def visit(k):
            nonlocal nodes
            nodes+=1
            if nodes%2048==0:
                if session.stop_requested: raise InterruptedError('用户已停止')
                if nodes>500000: raise OverflowError
            if k==len(order):
                m=len(chosen); totals[m]=totals.get(m,0)+1
                for i in chosen: counts[i][m]=counts[i].get(m,0)+1
                return
            i=order[k]
            for v in (0,1):
                valid=True
                for j in hits[i]:
                    left[j]-=1; needs[j]-=v
                    if not 0<=needs[j]<=left[j]: valid=False
                if v: chosen.append(i)
                if valid: visit(k+1)
                if v: chosen.pop()
                for j in hits[i]: left[j]+=1; needs[j]+=v
        try: visit(0)
        except OverflowError:
            risks={i:max([n/len(c) for c,n in local if i in c] or [(total-len(mines))/len(unknown)]) for i in unknown}
            i=min(risks,key=lambda i:(risks[i],len(near[i])))
            return [i],risks[i]
        if not totals: raise RuntimeError('棋盘约束无解。')
        models.append((totals,counts))
    def combine(a,b):
        out={}
        for i,ni in a.items():
            for j,nj in b.items(): out[i+j]=out.get(i+j,0)+ni*nj
        return out
    outside=unknown-frontier; size=len(outside); bombs=total-len(mines)
    def ways(n,k): return math.comb(n,k) if 0<=k<=n else 0
    joint={0:1}
    for totals,_ in models: joint=combine(joint,totals)
    denominator=sum(v*ways(size,bombs-k) for k,v in joint.items())
    if not denominator: raise RuntimeError('总雷数与识别结果不一致。')
    risk={}
    for idx,(totals,counts) in enumerate(models):
        others={0:1}
        for j,(t,_) in enumerate(models):
            if j!=idx: others=combine(others,t)
        for i,dist in counts.items():
            numerator=sum(n*v*ways(size,bombs-k-o) for k,n in dist.items() for o,v in others.items())
            risk[i]=numerator/denominator
    if outside:
        p=sum(v*ways(size-1,bombs-k-1) for k,v in joint.items())/denominator
        risk.update({i:p for i in outside})
    safe=[i for i,p in risk.items() if p==0]
    if safe:return safe,0
    i=min(risk,key=lambda i:(risk[i],len(near[i]),random.random()))
    return [i],risk[i]
