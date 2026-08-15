const DATA_CONFIG = {
    getDatesUrl: () => '/api/dates',
    getPapersUrl: (date, lang, page, pageSize, category, keyword, author) => {
        let url = `/api/papers?date=${encodeURIComponent(date)}&lang=${encodeURIComponent(lang)}`;
        if (page !== undefined && page !== null) {
            url += `&page=${encodeURIComponent(page)}`;
        }
        if (pageSize !== undefined && pageSize !== null) {
            url += `&page_size=${encodeURIComponent(pageSize)}`;
        }
        if (category && category !== 'all') {
            url += `&category=${encodeURIComponent(category)}`;
        }
        if (keyword) {
            url += `&keyword=${encodeURIComponent(keyword)}`;
        }
        if (author) {
            url += `&author=${encodeURIComponent(author)}`;
        }
        return url;
    },
    getPapersRangeUrl: (startDate, endDate, lang, page, pageSize, category, keyword, author) => {
        let url = `/api/papers/range?start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}&lang=${encodeURIComponent(lang)}`;
        if (page !== undefined && page !== null) {
            url += `&page=${encodeURIComponent(page)}`;
        }
        if (pageSize !== undefined && pageSize !== null) {
            url += `&page_size=${encodeURIComponent(pageSize)}`;
        }
        if (category && category !== 'all') {
            url += `&category=${encodeURIComponent(category)}`;
        }
        if (keyword) {
            url += `&keyword=${encodeURIComponent(keyword)}`;
        }
        if (author) {
            url += `&author=${encodeURIComponent(author)}`;
        }
        return url;
    }
};

