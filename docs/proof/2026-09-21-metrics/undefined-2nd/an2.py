import collections
B=collections.defaultdict(lambda:[0,0,0,0,0])
for ln in open('s2_q3.out'):
    p=ln.rstrip('\n').split('|')
    if p[0]=='B' and len(p)>=7:
        k=p[2]; v=B[k]
        v[0]+=1; v[1]+=int(p[3]); v[2]+=int(p[4]); v[3]+=int(p[5]); v[4]+=int(p[6])
print(f'{"тип":24s}{"кан":>6s}{"серий":>9s}{"отказ<=24ч":>11s}{"%A":>7s}{"обычных":>11s}{"отказ<=24ч":>11s}{"%B":>7s}{"A/B":>7s}')
tot=[0,0,0,0]
for k,v in sorted(B.items(), key=lambda x:-x[1][1]):
    if v[1]==0 or v[3]==0: continue
    a=100*v[2]/v[1]; b=100*v[4]/v[3]
    tot[0]+=v[1];tot[1]+=v[2];tot[2]+=v[3];tot[3]+=v[4]
    print(f'{k:24s}{v[0]:6d}{v[1]:9d}{v[2]:11d}{a:7.2f}{v[3]:11d}{v[4]:11d}{b:7.2f}{(a/b if b else float("nan")):7.2f}')
a=100*tot[1]/tot[0]; b=100*tot[3]/tot[2]
print(f'{"ИТОГО":24s}{"":6s}{tot[0]:9d}{tot[1]:11d}{a:7.2f}{tot[2]:11d}{tot[3]:11d}{b:7.2f}{a/b:7.2f}')
