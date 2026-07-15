def community_detection(nodes, links):
    if not nodes:
        return
        
    k = min(4, len(nodes))
    if k <= 1:
        for n in nodes:
            n["group"] = 0
        return
        
    # 1. 建立邻接矩阵作为每个关键词的特征向量 (Adjacency Matrix as Feature Vectors)
    node_ids = [n["id"] for n in nodes]
    node_idx = {nid: i for i, nid in enumerate(node_ids)}
    n_nodes = len(nodes)
    
    features = [[0.0] * n_nodes for _ in range(n_nodes)]
    for l in links:
        s, t, w = l["source"], l["target"], l["value"]
        if s in node_idx and t in node_idx:
            i, j = node_idx[s], node_idx[t]
            features[i][j] = float(w)
            features[j][i] = float(w)
            
    # 自环权重设为各节点边权重之和，体现节点自身重要性
    for i in range(n_nodes):
        features[i][i] = sum(features[i])
        
    # L2 归一化 (L2 Normalization)，计算欧氏距离即等价于余弦相似度
    for i in range(n_nodes):
        sq_sum = sum(val ** 2 for val in features[i])
        if sq_sum > 0:
            norm = sq_sum ** 0.5
            features[i] = [val / norm for val in features[i]]
            
    # 2. 运行标准的 K-Means 算法进行社群划分
    import random
    random.seed(42)  # 使用固定种子保证划分结果的确定性与稳定性
    
    # 随机选取 k 个节点作为初始质心
    centroids = [features[idx] for idx in random.sample(range(n_nodes), k)]
    
    assignments = [0] * n_nodes
    for _ in range(15):
        changed = False
        for i in range(n_nodes):
            min_dist = float('inf')
            best_c = 0
            for c_idx, c in enumerate(centroids):
                dist = sum((val1 - val2) ** 2 for val1, val2 in zip(features[i], c))
                if dist < min_dist:
                    min_dist = dist
                    best_c = c_idx
            if assignments[i] != best_c:
                assignments[i] = best_c
                changed = True
                
        if not changed:
            break
            
        # 更新质心 (Centroids Update)
        new_centroids = [[0.0] * n_nodes for _ in range(len(centroids))]
        counts = [0] * len(centroids)
        for i in range(n_nodes):
            c_idx = assignments[i]
            for j in range(n_nodes):
                new_centroids[c_idx][j] += features[i][j]
            counts[c_idx] += 1
            
        for c_idx in range(len(centroids)):
            if counts[c_idx] > 0:
                centroids[c_idx] = [val / counts[c_idx] for val in new_centroids[c_idx]]
                
    # 3. 将划分出来的组别映射回节点的 group 字段中
    for i, n in enumerate(nodes):
        n["group"] = assignments[i]
