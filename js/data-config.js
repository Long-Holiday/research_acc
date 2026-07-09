const DATA_CONFIG = {
    getDatesUrl: () => '/api/dates',
    getPapersUrl: (date, lang) => `/api/papers?date=${date}&lang=${lang}`
};
