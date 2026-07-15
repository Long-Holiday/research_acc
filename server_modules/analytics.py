def community_detection(nodes, links):
    """
    使用 Louvain 社区发现算法对关键词图网络进行社群划分，以自动发现最佳社区结构。
    """
    if not nodes:
        return
        
    if len(nodes) <= 1:
        for n in nodes:
            n["group"] = 0
        return
        
    import networkx as nx
    from networkx.algorithms.community import louvain_communities
    
    # 1. 建立无向图并添加节点
    G = nx.Graph()
    G.add_nodes_from([n["id"] for n in nodes])
    
    # 2. 添加带权重的边
    for l in links:
        s, t = l.get("source"), l.get("target")
        w = l.get("value", 1.0)
        if G.has_node(s) and G.has_node(t):
            # 若边已存在（为了防止多重边的情况），则累加其权重，否则新建边
            if G.has_edge(s, t):
                G[s][t]["weight"] += float(w)
            else:
                G.add_edge(s, t, weight=float(w))
                
    # 3. 运行 Louvain 社群发现算法进行划分，指定种子以保证稳定性
    communities = louvain_communities(G, weight="weight", seed=42)
    
    # 4. 将社群 ID 映射回各个节点的 group 字段中
    node_to_group = {}
    for group_idx, community in enumerate(communities):
        for node_id in community:
            node_to_group[node_id] = group_idx
            
    for n in nodes:
        n["group"] = node_to_group.get(n["id"], 0)
