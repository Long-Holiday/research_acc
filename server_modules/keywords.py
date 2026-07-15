import os
import re
import math
import spacy

STOPWORDS = {
    'method', 'based', 'towards', 'via', 'using', 'paper', 'propose', 'proposes',
    'proposed', 'approach', 'system', 'framework', 'result', 'show', 'shows',
    'demonstrated', 'demonstrates', 'demonstrate', 'experimental', 'experiment',
    'evaluation', 'performance', 'state', 'art', 'sota', 'dataset', 'task',
    'efficient', 'novel', 'modality', 'large', 'unsupervised', 'supervised',
    'semi', 'self', 'new', 'study', 'analysis', 'application', 'development',
    'design', 'process',
    
    'a', 'about', 'above', 'after', 'against', 'all', 'am', 'an', 'and', 'any', 'are', 'aren',
    'arent', 'as', 'at', 'be', 'because', 'been', 'before', 'being', 'below', 'between', 'both',
    'but', 'by', 'can', 'cannot', 'cant', 'could', 'couldn', 'couldnt', 'd', 'did', 'didn',
    'didnt', 'do', 'does', 'doesn', 'doesnt', 'doing', 'don', 'dont', 'down', 'during', 'each',
    'else', 'few', 'for', 'from', 'further', 'had', 'hadn', 'hadnt', 'has', 'hasn', 'hasnt',
    'have', 'haven', 'havent', 'having', 'he', 'hed', 'hell', 'hes', 'her', 'here', 'heres',
    'hers', 'herself', 'him', 'himself', 'his', 'how', 'hows', 'i', 'id', 'if', 'ill', 'im',
    'in', 'into', 'is', 'isn', 'isnt', 'it', 'its', 'itself', 'just', 'lets', 'll', 'm', 'me',
    'more', 'most', 'mustn', 'mustnt', 'my', 'myself', 'no', 'nor', 'not', 'now', 'o', 'of',
    'off', 'on', 'once', 'only', 'or', 'other', 'ought', 'our', 'ours', 'ourselves', 'out',
    'over', 'own', 're', 'same', 'shan', 'shant', 'she', 'shed', 'shell', 'shes', 'should',
    'shouldn', 'shouldnt', 'so', 'some', 'such', 't', 'than', 'that', 'thats', 'the', 'their',
    'theirs', 'them', 'themselves', 'then', 'there', 'theres', 'these', 'they', 'theyd',
    'theyll', 'theyre', 'theyve', 'this', 'those', 'through', 'to', 'too', 'under', 'until',
    'up', 've', 'very', 'was', 'wasn', 'wasnt', 'we', 'wed', 'well', 'were', 'weren', 'werent',
    'weve', 'what', 'whats', 'when', 'whens', 'where', 'wheres', 'which', 'while', 'who',
    'whos', 'whom', 'why', 'whys', 'will', 'with', 'won', 'wont', 'would', 'wouldn', 'wouldnt',
    'y', 'you', 'youd', 'youll', 'youre', 'youve', 'your', 'yours', 'yourself', 'yourselves'
}

try:
    nlp = spacy.load("en_core_web_sm")
except Exception as e:
    print(f"Failed to load spaCy model 'en_core_web_sm': {e}")
    nlp = None

idf_cache = {}
idf_doc_count = 0

def extract_candidates_spacy(title: str, summary: str = ""):
    if not nlp:
        return {}, {}
    
    candidates = {}
    raw_candidates = {}
    
    # Process title (weight = 3) and summary (weight = 1)
    for text, weight in [(title, 3), (summary, 1)]:
        if not text:
            continue
        text_lower = text.lower()
        
        try:
            doc = nlp(text_lower)
            
            # 1. Extract noun chunks (phrases)
            for chunk in doc.noun_chunks:
                cleaned_tokens = []
                raw_tokens = []
                for t in chunk:
                    if t.pos_ in ["NOUN", "PROPN", "ADJ"] and not t.is_stop and t.text not in STOPWORDS and len(t.lemma_) > 1:
                        cleaned_tokens.append(t.lemma_)
                        raw_tokens.append(t.text)
                        
                if cleaned_tokens:
                    phrase = " ".join(cleaned_tokens)
                    raw_phrase = " ".join(raw_tokens)
                    if 1 <= len(cleaned_tokens) <= 3:
                        candidates[phrase] = candidates.get(phrase, 0) + weight
                        if phrase not in raw_candidates:
                            raw_candidates[phrase] = {}
                        raw_candidates[phrase][raw_phrase] = raw_candidates[phrase].get(raw_phrase, 0) + 1
                        
            # 2. Extract individual nouns, adjectives, and proper nouns
            for t in doc:
                if t.pos_ in ["NOUN", "PROPN", "ADJ"] and not t.is_stop and t.text not in STOPWORDS and len(t.lemma_) > 1:
                    word = t.lemma_
                    raw_word = t.text
                    candidates[word] = candidates.get(word, 0) + weight
                    if word not in raw_candidates:
                        raw_candidates[word] = {}
                    raw_candidates[word][raw_word] = raw_candidates[word].get(raw_word, 0) + 1
        except Exception as e:
            print(f"Error in spaCy candidate extraction: {e}")
            
    return candidates, raw_candidates

def extract_keywords(title: str, summary: str = "", idf_map: dict = None) -> list:
    title = title or ""
    summary = summary or ""
    
    global idf_cache, idf_doc_count
    active_idf = idf_map if idf_map is not None else idf_cache
    default_idf = math.log((1 + idf_doc_count) / 2) + 1 if idf_doc_count > 0 else 1.0
    
    # 1. Try spaCy NLP approach
    if nlp is not None:
        try:
            candidates, raw_candidates = extract_candidates_spacy(title, summary)
            if candidates:
                # Calculate scores using TF-IDF
                scores = {}
                for stemmed, tf in candidates.items():
                    words_list = stemmed.split()
                    idf_val = sum(active_idf.get(w, default_idf) for w in words_list) / len(words_list)
                    scores[stemmed] = tf * idf_val
                
                # Subphrase pruning
                sorted_stems = sorted(candidates.keys(), key=len, reverse=True)
                pruned_stems = set()
                for i, long_stem in enumerate(sorted_stems):
                    if long_stem in pruned_stems:
                        continue
                    for short_stem in sorted_stems[i+1:]:
                        if short_stem in pruned_stems:
                            continue
                        long_words = long_stem.split()
                        short_words = short_stem.split()
                        is_sub = False
                        for idx in range(len(long_words) - len(short_words) + 1):
                            if long_words[idx:idx+len(short_words)] == short_words:
                                is_sub = True
                                break
                        
                        if is_sub:
                            if len(short_words) >= 2:
                                if scores[short_stem] <= scores[long_stem] * 1.2:
                                    pruned_stems.add(short_stem)
                                else:
                                    scores[short_stem] -= scores[long_stem]
                            else:
                                scores[short_stem] = max(0, scores[short_stem] - scores[long_stem] / 2)
                
                result = []
                for stemmed, score in scores.items():
                    if stemmed in pruned_stems or score <= 0:
                        continue
                    raw_phrases = raw_candidates.get(stemmed, {})
                    if raw_phrases:
                        best_raw = max(raw_phrases.items(), key=lambda x: x[1])[0]
                    else:
                        best_raw = stemmed
                    result.append((best_raw, score))
                
                result.sort(key=lambda x: x[1], reverse=True)
                return result[:10]
        except Exception as e:
            print(f"Error in spaCy keyword extraction: {e}")
            
    # 2. Fallback to basic stemming n-gram extraction (without spaCy)
    def stem_phrase(phrase: str) -> str:
        words = phrase.split()
        stemmed = []
        for w in words:
            w_stem = w.lower()
            if len(w_stem) > 4:
                if w_stem.endswith("sses"):
                    w_stem = w_stem[:-2]
                elif w_stem.endswith("ies"):
                    w_stem = w_stem[:-3] + "y"
                elif w_stem.endswith("s") and not w_stem.endswith("ss"):
                    w_stem = w_stem[:-1]
                
                if w_stem.endswith("ing"):
                    w_stem = w_stem[:-3]
                elif w_stem.endswith("ed"):
                    w_stem = w_stem[:-2]
            stemmed.append(w_stem)
        return " ".join(stemmed)

    raw_candidates = {}
    stemmed_freq = {}
    
    # Process title (weight = 3) and summary (weight = 1)
    for text, weight in [(title, 3), (summary, 1)]:
        if not text:
            continue
        text_lower = text.lower()
        cleaned = re.sub(r"[^\w\s-]", " ", text_lower)
        raw_words = cleaned.split()
        
        words = []
        for w in raw_words:
            w_clean = w.strip("-_")
            if w_clean and not w_clean.isdigit() and len(w_clean) > 1:
                words.append(w_clean)
                
        segments = []
        current_segment = []
        for w in words:
            if w in STOPWORDS:
                if current_segment:
                    segments.append(current_segment)
                    current_segment = []
            else:
                current_segment.append(w)
        if current_segment:
            segments.append(current_segment)
            
        for seg in segments:
            n = len(seg)
            for i in range(n):
                for l in range(1, min(4, n - i + 1)):
                    phrase = " ".join(seg[i:i+l])
                    stemmed = stem_phrase(phrase)
                    
                    stemmed_freq[stemmed] = stemmed_freq.get(stemmed, 0) + weight
                    if stemmed not in raw_candidates:
                        raw_candidates[stemmed] = {}
                    raw_candidates[stemmed][phrase] = raw_candidates[stemmed].get(phrase, 0) + 1

    # Calculate scores using TF-IDF for basic candidates
    scores = {}
    for stemmed, tf in stemmed_freq.items():
        words_list = stemmed.split()
        idf_val = sum(active_idf.get(w, default_idf) for w in words_list) / len(words_list)
        scores[stemmed] = tf * idf_val

    sorted_stems = sorted(stemmed_freq.keys(), key=len, reverse=True)
    pruned_stems = set()
    
    for i, long_stem in enumerate(sorted_stems):
        if long_stem in pruned_stems:
            continue
        for short_stem in sorted_stems[i+1:]:
            if short_stem in pruned_stems:
                continue
            long_words = long_stem.split()
            short_words = short_stem.split()
            is_sub = False
            for idx in range(len(long_words) - len(short_words) + 1):
                if long_words[idx:idx+len(short_words)] == short_words:
                    is_sub = True
                    break
            
            if is_sub:
                if len(short_words) >= 2:
                    if scores[short_stem] <= scores[long_stem] * 1.2:
                        pruned_stems.add(short_stem)
                    else:
                        scores[short_stem] -= scores[long_stem]
                else:
                    scores[short_stem] = max(0, scores[short_stem] - scores[long_stem] / 2)

    result = []
    for stemmed, score in scores.items():
        if stemmed in pruned_stems or score <= 0:
            continue
        raw_phrases = raw_candidates.get(stemmed, {})
        if raw_phrases:
            best_raw = max(raw_phrases.items(), key=lambda x: x[1])[0]
        else:
            best_raw = stemmed
        result.append((best_raw, score))
        
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:10]
