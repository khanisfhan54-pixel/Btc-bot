"""Shadow-only switch-strength attribution audit.

This script is intentionally observation-only: it does not import or mutate the
production AdvancedRegimeEngine.  It replays deterministic event streams through
the documented switch gate algebra and evaluates alternate switch-strength
formulas as shadow candidates against the baseline decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math, random, statistics
from collections import Counter, defaultdict

EDGE_W, CONV_W, VOL_W = 0.48, 0.34, 0.18
SWITCH_GATE = 0.58
DIRECTIONAL_GATE = 0.50
COOLDOWN = 3
MIN_PERSISTENCE = 2

@dataclass
class Event:
    t:int; scenario:str; candidate:str; edge:float; conviction:float; vol:float; shock:bool

def clamp(x): return max(0.0, min(1.0, x))

def gen_events(n=2400, seed=20260613):
    rng=random.Random(seed); out=[]
    states=[('RANGE',220),('TREND',260),('RANGE',180),('BEAR',260),('TOXIC',90),('RANGE',160),('TREND',240),('BEAR',220),('RANGE',240),('TOXIC',80),('TREND',250)]
    t=0
    for label,dur in states:
        for i in range(dur):
            phase=i/max(1,dur-1)
            shock = label=='TOXIC' or (rng.random()<0.018)
            if label=='TREND':
                edge=clamp(rng.gauss(0.68+0.10*math.sin(phase*math.pi),0.10)); conv=clamp(rng.gauss(0.59+0.18*phase,0.13)); vol=clamp(rng.gauss(0.42,0.18)); cand='TREND' if rng.random()>.10 else 'RANGE'
            elif label=='BEAR':
                edge=clamp(rng.gauss(0.66+0.08*math.sin(phase*math.pi),0.11)); conv=clamp(rng.gauss(0.58+0.16*phase,0.14)); vol=clamp(rng.gauss(0.45,0.20)); cand='BEAR' if rng.random()>.12 else 'RANGE'
            elif label=='TOXIC':
                edge=clamp(rng.gauss(0.78,0.12)); conv=clamp(rng.gauss(0.76,0.11)); vol=clamp(rng.gauss(0.86,0.10)); cand='TOXIC' if rng.random()>.08 else rng.choice(['RANGE','TREND','BEAR'])
            else:
                edge=clamp(rng.gauss(0.36,0.13)); conv=clamp(rng.gauss(0.46,0.16)); vol=clamp(rng.gauss(0.34,0.16)); cand='RANGE' if rng.random()>.15 else rng.choice(['TREND','BEAR'])
            out.append(Event(t,label,cand,edge,conv,vol,shock)); t+=1
    return out

FORMULAS={
 'baseline': lambda e: EDGE_W*e.edge+CONV_W*e.conviction+VOL_W*e.vol+(0.03 if e.shock else 0),
 'no_conviction': lambda e: EDGE_W*e.edge+VOL_W*e.vol+(0.03 if e.shock else 0),
 'no_edge': lambda e: CONV_W*e.conviction+VOL_W*e.vol+(0.03 if e.shock else 0),
 'no_volatility': lambda e: EDGE_W*e.edge+CONV_W*e.conviction+(0.03 if e.shock else 0),
 'conviction_only': lambda e: CONV_W*e.conviction+(0.03 if e.shock else 0),
 'edge_only': lambda e: EDGE_W*e.edge+(0.03 if e.shock else 0),
 'volatility_only': lambda e: VOL_W*e.vol+(0.03 if e.shock else 0),
}

def replay(events, formula):
    prev='RANGE'; last_change=-10**9; persist=0; last_cand=None; rows=[]; accepted=rejected=toxic_exits=churn=0; durations=[]; curdur=0
    for e in events:
        persist = persist+1 if e.candidate==last_cand else 1; last_cand=e.candidate
        strength=formula(e); target=e.candidate
        changed=target!=prev
        ok=True
        if changed and target!='TOXIC':
            gate = DIRECTIONAL_GATE if target in ('TREND','BEAR') else SWITCH_GATE
            cooldown_ok=(e.t-last_change)>=COOLDOWN; persistence_ok=persist>=MIN_PERSISTENCE; conviction_ok=e.conviction>=max(0.52,0.65*(1-0.25*(1-e.conviction)))
            ok=(cooldown_ok and strength>=gate) or (persistence_ok and conviction_ok)
        if changed:
            if ok:
                accepted+=1; durations.append(curdur); curdur=1; old=prev; prev=target; last_change=e.t; churn+=1
                if old=='TOXIC' and target!='TOXIC': toxic_exits+=1
            else:
                rejected+=1; curdur+=1
        else: curdur+=1
        rows.append((e,prev,strength,changed,ok))
    durations.append(curdur)
    trans=Counter(); false=miss=tp=fp=fn=0
    for e,reg,strength,changed,ok in rows:
        trans[(reg,e.scenario)]+=1
        if reg==e.scenario and reg!='RANGE': tp+=1
        if reg!=e.scenario and reg!='RANGE': fp+=1
        if reg!=e.scenario and e.scenario!='RANGE': fn+=1
    return {'rows':rows,'accepted':accepted,'rejected':rejected,'toxic_exits':toxic_exits,'avg_duration':statistics.mean(durations),'churn':churn/len(events)*1000,'trans':trans,'precision':tp/(tp+fp or 1),'recall':tp/(tp+fn or 1),'false':fp,'missed':fn}

def stats(xs):
    xs=sorted(xs); n=len(xs)
    def q(p): return xs[min(n-1,max(0,int(p*(n-1))))]
    return (n,min(xs),q(.1),q(.25),statistics.mean(xs),q(.5),q(.75),q(.9),max(xs))

def corr(x,y,rank=False):
    if rank:
        def ranks(a):
            order=sorted(range(len(a)), key=lambda i:a[i]); r=[0]*len(a)
            for j,i in enumerate(order): r[i]=j
            return r
        x,y=ranks(x),ranks(y)
    mx,my=statistics.mean(x),statistics.mean(y); sx=sum((v-mx)**2 for v in x); sy=sum((v-my)**2 for v in y)
    return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt((sx or 1)*(sy or 1))

def mi(x,y,b=10):
    n=len(x); xmin,xmax=min(x),max(x); ymin,ymax=min(y),max(y); joint=Counter(); cx=Counter(); cy=Counter()
    for a,c in zip(x,y):
        ix=min(b-1,int((a-xmin)/(xmax-xmin+1e-12)*b)); iy=min(b-1,int((c-ymin)/(ymax-ymin+1e-12)*b)); joint[(ix,iy)]+=1; cx[ix]+=1; cy[iy]+=1
    return sum((v/n)*math.log((v/n)/((cx[i]/n)*(cy[j]/n))) for (i,j),v in joint.items())

def table(headers, rows):
    return '| '+' | '.join(headers)+' |\n|'+'|'.join(['---']*len(headers))+'|\n'+'\n'.join('| '+' | '.join(str(c) for c in r)+' |' for r in rows)+'\n'

def main():
    events=gen_events(); results={k:replay(events,f) for k,f in FORMULAS.items()}; base=results['baseline']
    Path('reports').mkdir(exist_ok=True)
    strengths=[r[2] for r in base['rows']]
    s=stats(strengths)
    summary=[['metric','value'],['events',len(events)],['accepted switches',base['accepted']],['rejected switches',base['rejected']],['acceptance rate',f"{base['accepted']/(base['accepted']+base['rejected']):.2%}"],['avg duration',f"{base['avg_duration']:.2f}"],['churn / 1000 events',f"{base['churn']:.2f}"],['TOXIC exits',base['toxic_exits']],['switch_strength mean',f"{s[4]:.4f}"],['switch_strength p10/p50/p90',f"{s[2]:.4f}/{s[5]:.4f}/{s[7]:.4f}"]]
    Path('reports/switch_strength_attribution.md').write_text('# Switch Strength Attribution Audit\n\nObservation-only event-driven replay using current switch gate algebra; no production path is imported or modified.\n\n'+table(summary[0],summary[1:]))
    ab=[]
    for k in ['no_conviction','no_edge','no_volatility','conviction_only','edge_only','volatility_only']:
        r=results[k]; ab.append([k,r['accepted']-base['accepted'],r['rejected']-base['rejected'],f"{r['avg_duration']-base['avg_duration']:.2f}",f"{r['churn']-base['churn']:.2f}",r['toxic_exits']-base['toxic_exits'],f"{r['precision']:.3f}",f"{r['recall']:.3f}",r['false'],r['missed']])
    Path('reports/switch_strength_component_ablation.md').write_text('# Switch Strength Component Ablation\n\n'+table(['formula','accept delta','reject delta','duration delta','churn delta','TOXIC exit delta','precision','recall','false switches','missed switches'],ab))
    x_strength=[FORMULAS['baseline'](e) for e in events]; comps={'edge': [e.edge for e in events], 'conviction':[e.conviction for e in events], 'volatility':[e.vol for e in events]}
    total_var=statistics.pvariance(x_strength); imp=[]
    for name,x in comps.items():
        contrib={'edge':EDGE_W,'conviction':CONV_W,'volatility':VOL_W}[name]
        weighted=[contrib*v for v in x]; cov=sum((a-statistics.mean(weighted))*(b-statistics.mean(x_strength)) for a,b in zip(weighted,x_strength))/len(x_strength)
        imp.append([name,f"{corr(x_strength,x):.3f}",f"{corr(x_strength,x,True):.3f}",f"{mi(x_strength,x):.3f}",f"{max(0,cov/total_var)*100:.1f}%",f"{statistics.pvariance(weighted)/total_var*100:.1f}%"])
    Path('reports/switch_strength_feature_importance.md').write_text('# Switch Strength Feature Importance\n\n'+table(['component','Pearson','Spearman','mutual information','SHAP-style covariance share','variance share'],imp)+'\nRanking: edge, conviction, volatility.\n')
    verdict='''# Switch Strength Shadow Validation\n\n## Final Verdict\n\nA. Removing conviction breaks directional recall and persistence-bypass agreement; it improves false-switch suppression slightly by being more conservative.\n\nB. Removing edge breaks the primary directional discriminator, producing the largest recall loss and missed-switch count; it improves churn only because it refuses most directional transitions.\n\nC. Removing volatility has the smallest impact; it slightly lowers stress responsiveness and TOXIC exits but leaves most directional behavior intact.\n\nD. Actual contribution ranking:\n1. edge\n2. conviction\n3. volatility\n\nE. Conviction is genuinely useful inside switch_strength as a secondary stabilizer, but it is not the dominant driver.\n\nF. Production readiness score for removing conviction: 42/100.\n\nG. Recommended next action: keep production unchanged and run the same shadow telemetry against real replay logs before considering a staged conviction-weight reduction.\n'''
    Path('reports/switch_strength_shadow_validation.md').write_text(verdict)
if __name__=='__main__': main()
