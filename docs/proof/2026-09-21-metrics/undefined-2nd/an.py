import collections, statistics as st
A=collections.Counter(); Ak=collections.defaultdict(collections.Counter); B=[]
for ln in open('s2_q3.out'):
    p=ln.rstrip('\n').split('|')
    if p[0]=='A' and len(p)>=4:
        A[p[2]]+=int(p[3]); Ak[p[1]][p[2]]+=int(p[3])
    elif p[0]=='B' and len(p)>=7:
        B.append((int(p[1]),p[2],int(p[3]),int(p[4]),int(p[5]),int(p[6])))
tot=sum(A.values()); print('серий всего (тег A):',tot)
for v,c in A.most_common(12): print(f'  {v:22s} {c:9d} {100*c/tot:6.2f}%')
print()
def med(x): return st.median(x) if x else float('nan')
def report(rows,label,minruns=10,minbase=100):
    sel=[r for r in rows if r[2]>=minruns and r[4]>=minbase and (r[3]>0 or r[5]>0)]
    if not sel: print(f'{label}: 0 каналов в счёте'); return
    a=[r[3]/r[2] for r in sel]; b=[r[5]/r[4] for r in sel]; d=[x-y for x,y in zip(a,b)]
    pos=sum(1 for x in d if x>0); neg=sum(1 for x in d if x<0)
    print(f'{label}: каналов {len(sel)}; медиана после серии {100*med(a):.2f}%, после обычной {100*med(b):.2f}%, медиана разности {100*med(d):+.2f}%; выше {pos}, ниже {neg}')
print('=== ВЕСЬ ПАРК, порог: >=10 серий и >=100 обычных записей, и хотя бы один отказ в одной из групп')
report(B,'все типы')
print()
print('=== по типам')
kinds=sorted(set(r[1] for r in B))
for k in kinds: report([r for r in B if r[1]==k],f'  {k}')
print()
print('=== без требования отказа (только пороги наблюдений)')
def report2(rows,label,minruns=10,minbase=100):
    sel=[r for r in rows if r[2]>=minruns and r[4]>=minbase]
    if not sel: print(label,'пусто'); return
    a=[r[3]/r[2] for r in sel]; b=[r[5]/r[4] for r in sel]; d=[x-y for x,y in zip(a,b)]
    print(f'{label}: каналов {len(sel)}; медиана A {100*med(a):.2f}%, B {100*med(b):.2f}%, разность {100*med(d):+.2f}%')
report2(B,'все типы')
for k in ('Тепловой датчик','Датчик температуры','Газовый датчик','Датчик дыма'):
    report2([r for r in B if r[1]==k],'  '+k)
print()
print('=== закрытие серий по типам (доля Нормы / Неисправен)')
print(f'{"тип":24s}{"серий":>9s}{"Норма":>9s}{"Неиспр":>9s}{"%Норма":>8s}{"%Неиспр":>9s}  прочее')
for k,c in sorted(Ak.items(), key=lambda x:-sum(x[1].values())):
    s=sum(c.values()); oth=[f'{v} {n}' for v,n in c.most_common() if v not in ('Норма','Неисправен')][:3]
    print(f'{k:24s}{s:9d}{c["Норма"]:9d}{c["Неисправен"]:9d}{100*c["Норма"]/s:8.1f}{100*c["Неисправен"]/s:9.2f}  {"; ".join(oth)}')
