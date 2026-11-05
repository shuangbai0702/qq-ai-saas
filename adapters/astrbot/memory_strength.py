"""Read-time memory strengths: never permissions or model decision rules."""
DAY=86400
INTEREST_HALF_LIFE=2*DAY
LONG_INTEREST_HALF_LIFE=30*DAY
CONSOLIDATED_HITS=3
INTEREST_REINFORCE_INTERVAL=6*3600
EXPRESSION_HALF_LIFE=30*DAY
EXPRESSION_USE_INTERVAL=DAY
FACT_EVENT_HALF_LIFE=30*DAY
FACT_STATE_HALF_LIFE=2*DAY

def decay(weight,updated,now,half_life):
    return weight*2**(-max(0,now-updated)/half_life)

def interest_strength(weight,hits,updated,now,source=''):
    return decay(weight,updated,now,LONG_INTEREST_HALF_LIFE if hits>=CONSOLIDATED_HITS or source=='persona' else INTEREST_HALF_LIFE)

def relevance(topic,query):
    from hosted_memory import tokens
    return len(tokens(topic)&tokens(query))+(3 if topic and topic in query else 0)

def fact_strength(kind,updated,now,importance=.5):
    if kind=='stable':return 1.
    return decay(1.,updated,now,FACT_STATE_HALF_LIFE if kind=='state' else FACT_EVENT_HALF_LIFE*(1+2*importance))
