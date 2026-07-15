def community_detection(nodes, links):
    groups = {n["id"]: i for i, n in enumerate(nodes)}
    adj = {n["id"]: {} for n in nodes}
    for l in links:
        s, t, w = l["source"], l["target"], l["value"]
        if s in adj and t in adj:
            adj[s][t] = w
            adj[t][s] = w
            
    for _ in range(10):
        import random
        shuffled_nodes = [n["id"] for n in nodes]
        random.seed(42)
        random.shuffle(shuffled_nodes)
        
        for node in shuffled_nodes:
            if not adj[node]:
                continue
            label_weights = {}
            for neighbor, weight in adj[node].items():
                label = groups[neighbor]
                label_weights[label] = label_weights.get(label, 0) + weight
                
            if label_weights:
                best_label = max(label_weights.items(), key=lambda x: x[1])[0]
                groups[node] = best_label
                
    unique_groups = sorted(list(set(groups.values())))
    group_mapping = {g: i for i, g in enumerate(unique_groups)}
    
    for n in nodes:
        n["group"] = group_mapping[groups[n["id"]]]
