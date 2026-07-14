function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/[&<>'"]/g, 
    tag => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[tag] || tag)
  );
}

let currentDate = '';
let availableDates = [];
let paperData = {};
let flatpickrStartInstance = null;
let flatpickrEndInstance = null;
let allPapersData = [];
let selectedCategories = ['All'];

// Global variables for backend integration
let globalStartDate = '';
let globalEndDate = '';
let globalLang = 'en';
let keywordChartInstance = null;
let currentKeywordsData = [];
let currentDistDimension = 'category';

// Categories names are displayed directly from data keys to match the main page

document.addEventListener('DOMContentLoaded', () => {
  // Check screen size
  const checkScreenSize = () => {
    if (window.innerWidth < 768) {
      const warningModal = document.createElement('div');
      warningModal.className = 'screen-size-warning';
      warningModal.innerHTML = `
        <div class="warning-content">
          <h3>⚠️ Screen Size Notice</h3>
          <p>We've detected that you're using a device with a small screen. For the best data visualization experience, we recommend viewing this statistics page on a larger screen device (such as a tablet or computer).</p>
          <button onclick="this.parentElement.parentElement.remove()">Got it</button>
        </div>
      `;
      document.body.appendChild(warningModal);
    }
  };

  checkScreenSize();
  // Recheck on window resize
  window.addEventListener('resize', checkScreenSize);

  initEventListeners();
  
  fetchAvailableDates().then(() => {
    if (availableDates.length > 0) {
      const latestDateStr = availableDates[0];
      const latestDate = new Date(latestDateStr);
      const oneMonthAgo = new Date(latestDate);
      oneMonthAgo.setDate(oneMonthAgo.getDate() - 30);
      
      const oneMonthAgoStr = oneMonthAgo.getFullYear() + "-" + 
                             String(oneMonthAgo.getMonth() + 1).padStart(2, '0') + "-" + 
                             String(oneMonthAgo.getDate()).padStart(2, '0');
                             
      const datesInRange = availableDates.filter(d => d >= oneMonthAgoStr && d <= latestDateStr);
      const startDateStr = datesInRange.length > 0 ? datesInRange[datesInRange.length - 1] : latestDateStr;
      
      loadPapersByDateRange(startDateStr, latestDateStr);
    }
  });
});




function toggleDatePicker() {
  const datePicker = document.getElementById('datePickerModal');
  datePicker.classList.toggle('active');
  
  if (datePicker.classList.contains('active')) {
    document.body.style.overflow = 'hidden';
    
    // 重新初始化日期选择器以确保它反映最新的可用日期
    if (flatpickrStartInstance && flatpickrEndInstance && currentDate.includes(' - ')) {
      const dates = currentDate.split(' - ');
      flatpickrStartInstance.setDate(dates[0], false);
      flatpickrEndInstance.setDate(dates[1], false);
    } else if (flatpickrStartInstance && flatpickrEndInstance) {
      flatpickrStartInstance.setDate(currentDate, false);
      flatpickrEndInstance.setDate(currentDate, false);
    }
  } else {
    document.body.style.overflow = '';
  }
}

function initEventListeners() {
  // 只允许通过日历按钮打开日期选择器
  const calendarButton = document.getElementById('calendarButton');
  if (calendarButton) {
    calendarButton.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleDatePicker();
    });
  }
  
  // 点击模态框背景时关闭
  const datePickerModal = document.querySelector('.date-picker-modal');
  if (datePickerModal) {
    datePickerModal.addEventListener('click', (event) => {
      if (event.target === datePickerModal) {
        toggleDatePicker();
      }
    });
  }
  
  // 阻止日期选择器内容区域的点击事件冒泡
  const datePickerContent = document.querySelector('.date-picker-content');
  if (datePickerContent) {
    datePickerContent.addEventListener('click', (e) => {
      e.stopPropagation();
    });
  }
  
  const applyDateRangeBtn = document.getElementById('applyDateRange');
  if (applyDateRangeBtn) {
    applyDateRangeBtn.addEventListener('click', () => {
      if (flatpickrStartInstance && flatpickrEndInstance) {
        const startDates = flatpickrStartInstance.selectedDates;
        const endDates = flatpickrEndInstance.selectedDates;
        
        if (startDates.length > 0 && endDates.length > 0) {
          let startDate = formatDateForAPI(startDates[0]);
          let endDate = formatDateForAPI(endDates[0]);
          
          if (new Date(startDate) > new Date(endDate)) {
            // 交换日期
            const temp = startDate;
            startDate = endDate;
            endDate = temp;
          }
          
          loadPapersByDateRange(startDate, endDate);
          toggleDatePicker();
        } else {
          alert('Please select both start and end dates.');
        }
      }
    });
  }
  
  // 添加侧边栏关闭按钮事件
  const closeButton = document.querySelector('.close-sidebar');
  if (closeButton) {
    closeButton.addEventListener('click', closeSidebar);
  }
  
  // 点击侧边栏外部时关闭侧边栏
  document.addEventListener('click', (event) => {
    const sidebar = document.getElementById('paperSidebar');
    const isClickInside = sidebar.contains(event.target);
    const isClickOnKeyword = event.target.closest('.keyword-item') || 
                            event.target.closest('.keyword-cloud text');
    
    if (!isClickInside && !isClickOnKeyword && sidebar.classList.contains('active')) {
      closeSidebar();
    }
  });
}

// Function to detect preferred language based on browser settings
function getPreferredLanguage() {
  const browserLang = navigator.language || navigator.userLanguage;
  // Check if browser is set to Chinese variants
  if (browserLang.startsWith('zh')) {
    return 'Chinese';
  }
  // Default to English for all other languages
  return 'English';
}

// Function to select the best available language for a date
function selectLanguageForDate(date, preferredLanguage = null) {
  const availableLanguages = window.dateLanguageMap?.get(date) || [];
  
  if (availableLanguages.length === 0) {
    return 'en'; // fallback
  }
  
  // Use provided preference or detect from browser
  const preferred = preferredLanguage || getPreferredLanguage();
  
  // 如果首选是 Chinese，优先尝试匹配 'zh' 或 'Chinese'
  if (preferred === 'Chinese') {
    if (availableLanguages.includes('zh')) return 'zh';
    if (availableLanguages.includes('Chinese')) return 'Chinese';
  }
  
  // 如果首选是 English，优先尝试匹配 'en' 或 'English'
  if (preferred === 'English') {
    if (availableLanguages.includes('en')) return 'en';
    if (availableLanguages.includes('English')) return 'English';
  }

  // If preferred language is available, use it
  if (availableLanguages.includes(preferred)) {
    return preferred;
  }
  
  // NOTE: The statistics page must prefer loading the English version of data where possible,
  // because the 'compromise' NLP library used for keyword extraction only supports English taggers.
  // Fallback: prefer English/en if available, otherwise use the first available
  if (availableLanguages.includes('en')) return 'en';
  if (availableLanguages.includes('English')) return 'English';
  return availableLanguages[0];
}

function normalizePaper(paper, date) {
  const allCategories = Array.isArray(paper.categories) 
    ? paper.categories 
    : (paper.categories ? [paper.categories] : []);
  
  const summary = paper.AI && paper.AI.tldr ? paper.AI.tldr : (paper.summary || '');
  
  return {
    title: paper.title || '',
    translated_title: paper.AI && paper.AI.translated_title ? paper.AI.translated_title : '',
    url: paper.url || paper.abs || paper.pdf || `https://arxiv.org/abs/${paper.id}`,
    authors: Array.isArray(paper.authors) ? paper.authors.join(', ') : (paper.authors || ''),
    category: allCategories,
    summary: summary,
    details: paper.summary || '',
    date: date || paper.date || '',
    id: paper.id || '',
    motivation: paper.AI && paper.AI.motivation ? paper.AI.motivation : '',
    method: paper.AI && paper.AI.method ? paper.AI.method : '',
    result: paper.AI && paper.AI.result ? paper.AI.result : '',
    conclusion: paper.AI && paper.AI.conclusion ? paper.AI.conclusion : '',
    remote_sensing_cross: paper.AI && paper.AI.remote_sensing_cross ? paper.AI.remote_sensing_cross : '',
    code_url: paper.code_url || '',
    code_stars: paper.code_stars || 0,
    code_last_update: paper.code_last_update || '',
    abstract_zh: paper.abstract_zh || ''
  };
}

async function fetchAvailableDates() {
  try {
    const datesUrl = DATA_CONFIG.getDatesUrl();
    const response = await Auth.fetchWithAuth(datesUrl);
    if (!response.ok) {
      console.error('Error fetching dates:', response.status);
      return [];
    }
    const data = await response.json();
    const dateLanguageMap = new Map(Object.entries(data.languages || {}));
    window.dateLanguageMap = dateLanguageMap;
    
    availableDates = data.dates || [];
    availableDates.sort((a, b) => new Date(b) - new Date(a));

    initDatePicker();

    return availableDates;
  } catch (error) {
    console.error('获取可用日期失败:', error);
  }
}

function initDatePicker() {
  const startInput = document.getElementById('startDatePicker');
  const endInput = document.getElementById('endDatePicker');
  
  if (flatpickrStartInstance) flatpickrStartInstance.destroy();
  if (flatpickrEndInstance) flatpickrEndInstance.destroy();
  
  // 创建可用日期的映射，用于禁用无效日期
  const enabledDatesMap = {};
  availableDates.forEach(date => {
    enabledDatesMap[date] = true;
  });
  
  // 默认加载最近一个月
  let defaultEndDate = availableDates.length > 0 ? availableDates[0] : null;
  let defaultStartDate = defaultEndDate;
  
  if (availableDates.length > 0) {
    const latestDate = new Date(defaultEndDate);
    const oneMonthAgo = new Date(latestDate);
    oneMonthAgo.setDate(oneMonthAgo.getDate() - 30);
    
    const oneMonthAgoStr = oneMonthAgo.getFullYear() + "-" + 
                           String(oneMonthAgo.getMonth() + 1).padStart(2, '0') + "-" + 
                           String(oneMonthAgo.getDate()).padStart(2, '0');
                           
    const datesInRange = availableDates.filter(d => d >= oneMonthAgoStr && d <= defaultEndDate);
    defaultStartDate = datesInRange.length > 0 ? datesInRange[datesInRange.length - 1] : defaultEndDate;
  }
  
  const commonConfig = {
    inline: true,
    dateFormat: "Y-m-d",
    enable: [
      function(date) {
        // 只启用有效日期
        const dateStr = date.getFullYear() + "-" + 
                        String(date.getMonth() + 1).padStart(2, '0') + "-" + 
                        String(date.getDate()).padStart(2, '0');
        return !!enabledDatesMap[dateStr];
      }
    ]
  };
  
  // 配置 Flatpickr
  flatpickrStartInstance = flatpickr(startInput, {
    ...commonConfig,
    defaultDate: defaultStartDate
  });
  
  flatpickrEndInstance = flatpickr(endInput, {
    ...commonConfig,
    defaultDate: defaultEndDate
  });
}

function formatDateForAPI(date) {
  return date.getFullYear() + "-" + 
         String(date.getMonth() + 1).padStart(2, '0') + "-" + 
         String(date.getDate()).padStart(2, '0');
}

// 提取关键词并进行总结
const extractKeywords = (text) => {
  if (!text || typeof text !== 'string') return [];
  
  // 检查 nlp 是否定义以防止加载失败崩溃
  if (typeof nlp === 'undefined') {
    console.warn('compromise library (nlp) is not defined, using fallback keyword extraction.');
    return text.toLowerCase()
      .replace(/[^\w\s]/g, ' ')
      .split(/\s+/)
      .filter(w => w.length > 3 && !['with', 'from', 'that', 'this', 'learning', 'neural', 'network', 'model', 'data', 'using', 'based'].includes(w))
      .slice(0, 10);
  }

  // 移除特殊字符和多余空格
  const cleanText = text.replace(/[^\w\s]/g, ' ').replace(/\s+/g, ' ').trim();
  
  // 使用 compromise 进行文本处理
  let doc;
  try {
    doc = nlp(cleanText);
  } catch (err) {
    console.error('nlp parsing failed:', err);
    return [];
  }
  
  // 提取名词短语和重要词汇
  const terms = new Set();
  
  // 提取名词短语
  try {
    doc.match('#Noun+').forEach(match => {
      const phrase = match.text().toLowerCase();
      if (phrase.split(' ').length <= 3) { // 最多3个词的短语
        terms.add(phrase);
      }
    });
  } catch (err) {
    console.error('doc.match(#Noun+) iteration failed:', err);
  }
  
  // 提取形容词+名词组合
  try {
    doc.match('(#Adjective+ #Noun+)').forEach(match => {
      const phrase = match.text().toLowerCase();
      if (phrase.split(' ').length <= 3) {
        terms.add(phrase);
      }
    });
  } catch (err) {
    console.error('doc.match(#Adjective+ #Noun+) iteration failed:', err);
  }
  
  // 定义停用词
  const stopWords = new Set([
    'the', 'is', 'at', 'which', 'and', 'or', 'in', 'to', 'for', 'of', 
    'with', 'by', 'on', 'this', 'that', 'our', 'method', 'based', 
    'towards', 'via', 'multi', 'text', 'using', 'aware', 'data', 'from',
    'paper', 'propose', 'proposed', 'approach', 'model', 'system', 
    'framework', 'results', 'show', 'demonstrates', 'experimental', 
    'experiments', 'evaluation', 'performance', 'state', 'art', 'sota',
    'dataset', 'datasets', 'task', 'tasks', 'learning', 'neural', 
    'network', 'networks', 'deep', 'machine', 'artificial', 'intelligence', 
    'ai', 'ml', 'dl'
  ]);
  
  // 过滤停用词和短词
  const filteredTerms = Array.from(terms).filter(term => {
    const words = term.split(' ');
    return words.every(word => word.length > 2) && 
           !words.every(word => stopWords.has(word));
  });
  
  // 统计词频
  const termFreq = {};
  filteredTerms.forEach(term => {
    termFreq[term] = (termFreq[term] || 0) + 1;
    // 给多词短语更高的权重
    if (term.includes(' ')) {
      termFreq[term] *= 1.5;
    }
  });
  
  // 计算 TF 值（词频）
  const tfScores = {};
  const totalTerms = Object.values(termFreq).reduce((a, b) => a + b, 0);
  if (totalTerms > 0) {
    Object.entries(termFreq).forEach(([term, freq]) => {
      tfScores[term] = freq / totalTerms;
    });
  }
  
  // 按 TF 值排序并返回前10个关键词/短语
  return Object.entries(tfScores)
    .sort(([,a], [,b]) => b - a)
    .slice(0, 10)
    .map(([term]) => term);
};

async function loadPapersByDateRange(startDate, endDate) {
  // 获取日期范围内的所有有效日期
  const validDatesInRange = availableDates.filter(date => {
    return date >= startDate && date <= endDate;
  });
  
  if (validDatesInRange.length === 0) {
    alert('No available papers in the selected date range.');
    return;
  }

  // 保存全局统计 API 所需的参数
  globalStartDate = startDate;
  globalEndDate = endDate;
  globalLang = selectLanguageForDate(validDatesInRange[0] || startDate);
  
  if (startDate === endDate) {  
    currentDate = startDate;
    document.getElementById('currentDate').textContent = formatDate(startDate);
  } else {
    currentDate = `${startDate} - ${endDate}`;
    document.getElementById('currentDate').textContent = `${formatDate(startDate)} - ${formatDate(endDate)}`;
  }
  
  const container = document.getElementById('papersList');
  container.innerHTML = `
    <div class="loading-container">
      <div class="loading-spinner"></div>
      <p>Loading papers from ${formatDate(startDate)} to ${formatDate(endDate)}...</p>
    </div>
  `;
  
  try {
    // 加载时间范围内所有论文数据
    const allPaperData = {};
    allPapersData = []; // 重置全局论文数据
    
    const dataUrl = `/api/papers/range?start_date=${startDate}&end_date=${endDate}&lang=${globalLang}`;
    const response = await Auth.fetchWithAuth(dataUrl);
    if (!response.ok) {
      throw new Error(`Failed to fetch papers for range: ${response.statusText}`);
    }
    const dataPapers = await response.json();
    if (Array.isArray(dataPapers)) {
      const normalized = dataPapers.map(p => normalizePaper(p, p.date));
      
      normalized.forEach(paper => {
        if (paper.category && paper.category.length > 0) {
          const primaryCategory = paper.category[0];
          if (!allPaperData[primaryCategory]) {
            allPaperData[primaryCategory] = [];
          }
          allPaperData[primaryCategory].push(paper);
        }
        allPapersData.push(paper);
      });
    }
    
    paperData = allPaperData;
    
    // 渲染 Category tabs 并展示统计信息
    renderCategoryTabs(validDatesInRange);
    
  } catch (error) {
    console.error('加载论文数据失败:', error);
    container.innerHTML = `
      <div class="loading-container">
        <p>Loading data fails. Please retry.</p>
        <p>Error messages: ${error.message}</p>
      </div>
    `;
  }
}

function renderCategoryTabs(validDatesInRange) {
  const container = document.getElementById('papersList');
  
  // 1. 收集所有包含的类别 (仅基于主分类 primaryCategory)
  const categoriesSet = new Set();
  allPapersData.forEach(paper => {
    if (paper.category && paper.category.length > 0) {
      categoriesSet.add(paper.category[0]);
    }
  });
  
  // 过滤一些空白或无效的分类并排序
  const availableCategories = Array.from(categoriesSet)
    .filter(cat => cat && cat.trim().length > 0)
    .sort();
  
  // 2. 生成 HTML 结构
  container.innerHTML = `
    <div class="category-filter-wrapper">
      <div class="category-filter-title">选择论文分类 / Select Category</div>
      <div class="category-tabs" id="categoryTabs">
        <button class="category-tab active" data-category="All">
          <span class="tab-name">All Categories (全部)</span>
          <span class="tab-count">${allPapersData.length}</span>
        </button>
        ${availableCategories.map(cat => {
          const papersInCat = allPapersData.filter(paper => paper.category && paper.category[0] === cat);
          const displayName = cat;
          return `
            <button class="category-tab" data-category="${cat}">
              <span class="tab-name">${displayName}</span>
              <span class="tab-count">${papersInCat.length}</span>
            </button>
          `;
        }).join('')}
      </div>
    </div>
  `;

  // 3. 绑定 Tab 点击事件
  const tabs = document.querySelectorAll('.category-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', (e) => {
      const target = e.currentTarget;
      const cat = target.getAttribute('data-category');
      
      if (cat === 'All') {
        selectedCategories = ['All'];
        tabs.forEach(t => t.classList.remove('active'));
        target.classList.add('active');
      } else {
        // Remove 'All' if present
        if (selectedCategories.includes('All')) {
          selectedCategories = [];
          document.querySelector('.category-tab[data-category="All"]')?.classList.remove('active');
        }
        
        if (selectedCategories.includes(cat)) {
          // Deselect
          selectedCategories = selectedCategories.filter(c => c !== cat);
          target.classList.remove('active');
          
          // If empty, revert to 'All'
          if (selectedCategories.length === 0) {
            selectedCategories = ['All'];
            document.querySelector('.category-tab[data-category="All"]')?.classList.add('active');
          }
        } else {
          // Select
          selectedCategories.push(cat);
          target.classList.add('active');
        }
      }
      
      renderCategoryStats(selectedCategories, validDatesInRange);
    });
  });

  // 4. 恢复选中状态或默认 "All"
  const validSelections = selectedCategories.filter(cat => cat === 'All' || availableCategories.includes(cat));
  if (validSelections.length === 0) {
    selectedCategories = ['All'];
  } else {
    selectedCategories = validSelections;
  }
  
  tabs.forEach(t => {
    const cat = t.getAttribute('data-category');
    if (selectedCategories.includes(cat)) {
      t.classList.add('active');
    } else {
      t.classList.remove('active');
    }
  });

  renderCategoryStats(selectedCategories, validDatesInRange);
}

async function renderCategoryStats(categories, validDatesInRange) {
  const isAll = categories.includes('All');
  
  const filteredPapers = isAll 
    ? allPapersData 
    : allPapersData.filter(paper => paper.category && categories.includes(paper.category[0]));
     
  const hotKeywordsList = document.getElementById('hotKeywordsList');
  const trendChartCard = document.getElementById('trendChartCard');
  const netContainer = document.getElementById('networkContainer');
  const distSection = document.getElementById('keywordDistributionSection');

  // 无数据处理
  if (filteredPapers.length === 0) {
    const noDataHTML = `
      <div class="no-data" style="padding: 20px; text-align: center; color: var(--text-secondary);">
        <p>当前分类下暂无论文数据 / No papers in this category.</p>
      </div>
    `;
    if (hotKeywordsList) hotKeywordsList.innerHTML = noDataHTML;
    if (trendChartCard) trendChartCard.innerHTML = noDataHTML;
    if (netContainer) netContainer.innerHTML = noDataHTML;
    if (distSection) distSection.style.display = 'none';
    return;
  }

  // 1. 展示 Loading 状态，更新分类名称
  const categoryDisplayName = isAll ? 'All Categories' : categories.join(', ');
  
  const hotKeywordsCategoryName = document.getElementById('hotKeywordsCategoryName');
  if (hotKeywordsCategoryName) hotKeywordsCategoryName.textContent = categoryDisplayName;
  
  const trendCategoryName = document.getElementById('trendCategoryName');
  if (trendCategoryName) trendCategoryName.textContent = categoryDisplayName;
  
  const networkCategoryName = document.getElementById('networkCategoryName');
  if (networkCategoryName) networkCategoryName.textContent = categoryDisplayName;
  
  const distributionCategoryName = document.getElementById('distributionCategoryName');
  if (distributionCategoryName) distributionCategoryName.textContent = categoryDisplayName;

  const loadingSpinnerHTML = `
    <div class="loading-container" style="display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 150px; width: 100%;">
      <div class="loading-spinner"></div>
      <p style="margin-top: 10px; color: var(--text-secondary); font-size: 14px;">正在加载 / Loading...</p>
    </div>
  `;

  if (hotKeywordsList) hotKeywordsList.innerHTML = loadingSpinnerHTML;
  if (trendChartCard) trendChartCard.innerHTML = loadingSpinnerHTML;
  if (netContainer) netContainer.innerHTML = loadingSpinnerHTML;
  
  if (distSection) {
      distSection.style.display = 'block';
      const canvas = document.getElementById('keywordDistributionChart');
      if (canvas) {
          const ctx = canvas.getContext('2d');
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.fillStyle = 'var(--text-secondary, #666)';
          ctx.font = '14px sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText('Loading distribution chart...', canvas.width / 2, canvas.height / 2);
      }
  }

  try {
    // 2. 发起 API 请求获取关键词和趋势数据
    const categoryParam = isAll ? 'All' : categories.join(',');
    const keywordsUrl = `/api/stats/keywords?start_date=${encodeURIComponent(globalStartDate)}&end_date=${encodeURIComponent(globalEndDate)}&lang=${encodeURIComponent(globalLang)}&category=${encodeURIComponent(categoryParam)}`;
    const response = await Auth.fetchWithAuth(keywordsUrl);
    
    if (!response.ok) {
        throw new Error(`Failed to load keyword stats: ${response.statusText}`);
    }
    
    const data = await response.json();
    const keywords = data.keywords || [];
    currentKeywordsData = keywords;
    updateDistTabUI(currentDistDimension);
    const dailyTrends = data.daily_trends || [];
    
    const excludeSelect = document.getElementById('excludeKeywordsSelect');
    if (excludeSelect) {
      const choicesData = keywords.slice(0, 100).map(kw => ({
        value: kw.keyword,
        label: kw.keyword
      }));
      
      const savedExcludesRaw = localStorage.getItem('excludedKeywords');
      const savedExcludes = savedExcludesRaw ? JSON.parse(savedExcludesRaw) : [];
      
      const choicesSet = new Set(choicesData.map(c => c.value));
      savedExcludes.forEach(ex => {
        if (!choicesSet.has(ex)) {
          choicesData.push({ value: ex, label: ex });
        }
      });
      
      if (window.excludeChoices) {
        window.excludeChoices.clearChoices();
        window.excludeChoices.setChoices(choicesData, 'value', 'label', true);
        if (savedExcludes.length > 0) {
          window.excludeChoices.setChoiceByValue(savedExcludes);
        }
      } else {
        excludeSelect.innerHTML = '';
        choicesData.forEach(c => {
          const opt = document.createElement('option');
          opt.value = c.value; opt.textContent = c.label;
          excludeSelect.appendChild(opt);
        });
        window.excludeChoices = new Choices(excludeSelect, {
          removeItemButton: true,
          searchPlaceholderValue: 'Search keywords...',
          placeholderValue: 'Select keywords to exclude',
          shouldSort: false
        });
        
        if (savedExcludes.length > 0) {
          window.excludeChoices.setChoiceByValue(savedExcludes);
        }
        
        // Ensure change event triggers chart update
        excludeSelect.addEventListener('change', updateExcludeKeywords);
      }
    }
    
    if (keywords.length === 0) {
      const noKeywordsHTML = `
        <div class="no-data" style="padding: 20px; text-align: center; color: var(--text-secondary);">
          <p>当前分类下暂无热门关键词 / No keywords found in this category.</p>
        </div>
      `;
      if (hotKeywordsList) hotKeywordsList.innerHTML = noKeywordsHTML;
      if (trendChartCard) trendChartCard.innerHTML = noKeywordsHTML;
      if (distSection) distSection.style.display = 'none';
      if (netContainer) netContainer.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-secondary);">No sufficient keyword data.</div>';
      return;
    }

    // 准备热门关键词列表数据 (前 30 个)
    const keywordCloudData = keywords.slice(0, 30).map(item => ({
      text: item.keyword,
      size: Math.max(12, Math.min(50, item.count * 3))
    }));

    // 3. 准备折线图数据 (trendData)
    const top10Keywords = keywords.slice(0, 10).map(d => d.keyword);
    const trendMap = new Map();
    top10Keywords.forEach(k => trendMap.set(k, []));
    
    dailyTrends.forEach(item => {
      if (trendMap.has(item.keyword)) {
        trendMap.get(item.keyword).push({
          date: new Date(item.date + 'T00:00:00Z'),
          count: item.count
        });
      }
    });
    
    const trendData = Array.from(trendMap.entries()).map(([keyword, values]) => ({
      keyword: keyword,
      values: values.sort((a, b) => a.date - b.date)
    }));

    const hasMultipleDates = validDatesInRange.length > 1;

    // 4. 渲染热门关键词列表
    if (hotKeywordsList) {
      hotKeywordsList.innerHTML = keywordCloudData.length > 0 ? keywordCloudData.map((item, index) => `
        <div class="keyword-item" onclick="showRelatedPapers('${escapeHtml(item.text)}')">
          <span class="keyword-rank">${index + 1}</span>
          <span class="keyword-text">${escapeHtml(item.text)}</span>
          <span class="keyword-count">${keywords[index].count}</span>
        </div>
      `).join('') : '<p class="no-data">当前分类暂无热门关键词 / No keywords found.</p>';
    }
    
    // 渲染折线图趋势
    if (trendChartCard) {
      if (hasMultipleDates && top10Keywords.length > 0) {
        trendChartCard.innerHTML = `<div id="trendChart" style="width: 100%; height: 100%;"></div>`;
        setTimeout(() => {
          drawTrendChart(trendData, validDatesInRange);
        }, 50);
      } else {
        trendChartCard.innerHTML = `
          <div class="no-data" style="text-align: center; color: var(--text-secondary); line-height: 1.6; font-size: 14px; padding: 20px;">
            📈 暂无趋势图：趋势图需要选择至少两天的数据来进行对比分析。<br>
            Current selection contains only 1 day of data. Trend chart requires at least 2 days of data.
          </div>
        `;
      }
    }

    // 绘制关键词离散分布水平条形图
    if (distSection) {
      distSection.style.display = 'block';
      setTimeout(() => {
        drawDistributionChart(keywords);
      }, 50);
    }

    // 5. 加载并渲染网络图
    if (netContainer) {
      setTimeout(async () => {
        try {
          const categoryParam = isAll ? 'All' : categories.join(',');
          const networkUrl = `/api/stats/network?start_date=${encodeURIComponent(globalStartDate)}&end_date=${encodeURIComponent(globalEndDate)}&lang=${encodeURIComponent(globalLang)}&category=${encodeURIComponent(categoryParam)}`;
          const netResponse = await Auth.fetchWithAuth(networkUrl);
          if (!netResponse.ok) throw new Error("Failed to fetch network data");
          const networkData = await netResponse.json();
          renderNetwork(networkData);
        } catch (netErr) {
          console.error("加载网络图失败:", netErr);
          netContainer.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-secondary);">加载网络图失败 / Failed to load network: ${netErr.message}</div>`;
        }
      }, 50);
    }

  } catch (error) {
    console.error('加载统计数据失败:', error);
    const errorHTML = `
      <div class="error" style="padding: 20px; text-align: center; color: #e74c3c;">
        <p>加载统计数据失败 / Failed to load statistics.</p>
        <p>错误信息: ${error.message}</p>
      </div>
    `;
    if (hotKeywordsList) hotKeywordsList.innerHTML = errorHTML;
    if (trendChartCard) trendChartCard.innerHTML = errorHTML;
    if (distSection) distSection.style.display = 'none';
    if (netContainer) netContainer.innerHTML = '<div style="padding:20px;text-align:center;color:red;">加载共现网络失败。</div>';
  }
}

function getPalette(count) {
  // 使用黄金角（Golden Angle, 约 137.5°）步进色相，确保任意相邻关键词颜色对比最大化
  // 配合交替变化的饱和度和亮度，产生极具辨识度的丰富配色
  const colors = [];
  for (let i = 0; i < count; i++) {
    const hue = (i * 137.508) % 360;
    // 饱和度在 68% 到 86% 之间交替变化
    const saturation = 68 + (i % 3) * 9;
    // 亮度在 46% 到 58% 之间交替，提供明暗对比
    const lightness = 46 + (i % 2) * 12;
    colors.push(`hsl(${Math.round(hue)}, ${saturation}%, ${lightness}%)`);
  }
  return colors;
}

function updateDistTabUI(dimension) {
  const btnCategory = document.getElementById('btnDistCategory');
  const btnDate = document.getElementById('btnDistDate');
  if (!btnCategory || !btnDate) return;
  
  if (dimension === 'category') {
    btnCategory.style.background = 'var(--card-bg-color, #ffffff)';
    btnCategory.style.color = 'var(--text-color, #1e293b)';
    btnCategory.style.boxShadow = '0 1px 3px rgba(0,0,0,0.1)';
    
    btnDate.style.background = 'transparent';
    btnDate.style.color = 'var(--text-secondary, #64748b)';
    btnDate.style.boxShadow = 'none';
  } else {
    btnDate.style.background = 'var(--card-bg-color, #ffffff)';
    btnDate.style.color = 'var(--text-color, #1e293b)';
    btnDate.style.boxShadow = '0 1px 3px rgba(0,0,0,0.1)';
    
    btnCategory.style.background = 'transparent';
    btnCategory.style.color = 'var(--text-secondary, #64748b)';
    btnCategory.style.boxShadow = 'none';
  }
}

function updateExcludeKeywords() {
  if (window.excludeChoices) {
    const selectedValues = window.excludeChoices.getValue(true);
    localStorage.setItem('excludedKeywords', JSON.stringify(selectedValues || []));
  }
  
  if (currentKeywordsData && currentKeywordsData.length > 0) {
    drawDistributionChart(currentKeywordsData);
  }
}

function changeDistDimension(dimension) {
  if (currentDistDimension === dimension) return;
  currentDistDimension = dimension;
  updateDistTabUI(dimension);
  
  if (currentKeywordsData && currentKeywordsData.length > 0) {
    drawDistributionChart(currentKeywordsData);
  }
}

function drawDistributionChart(keywords) {
  try {
    const canvas = document.getElementById('keywordDistributionChart');
    if (!canvas) return;

    // 销毁旧实例
    if (keywordChartInstance) {
      keywordChartInstance.destroy();
      keywordChartInstance = null;
    }

    if (!keywords || keywords.length === 0) {
      console.warn('drawDistributionChart: keywords array is empty');
      return;
    }

    // 获取被排除的关键词
    let excludedSet = new Set();
    const excludeSelect = document.getElementById('excludeKeywordsSelect');
    if (excludeSelect) {
      Array.from(excludeSelect.selectedOptions).forEach(opt => {
        excludedSet.add(opt.value);
      });
    }

    // 过滤掉被排除的关键词
    const filteredKeywords = keywords.filter(kw => !excludedSet.has(kw.keyword));

    // 选取前 60 个最重要的关键词作为堆叠段以确保图表的可读性与美感
    const topKeywordsCount = 60;
    const topKeywords = filteredKeywords.slice(0, topKeywordsCount);

    let datasets = [];
    let labels = [];
    const dimension = currentDistDimension;

    // 1. 收集分类或日期列表作为 Y 轴 labels
    if (dimension === 'category') {
      const catsSet = new Set();
      keywords.forEach(d => {
        const dist = d.category_distribution || {};
        Object.keys(dist).forEach(cat => {
          if (cat && cat.trim() !== '') {
            catsSet.add(cat);
          }
        });
      });
      labels = Array.from(catsSet).sort();
    } else {
      const datesSet = new Set();
      keywords.forEach(d => {
        const dist = d.date_distribution || {};
        Object.keys(dist).forEach(date => {
          if (date && date.trim() !== '') {
            datesSet.add(date);
          }
        });
      });
      labels = Array.from(datesSet).sort();
    }

    // 2. 计算每个 label 下前 15 个关键词的频次总和，用于计算百分比
    const labelTotals = {};
    labels.forEach(label => {
      let total = 0;
      topKeywords.forEach(kwObj => {
        const dist = (dimension === 'category') 
          ? (kwObj.category_distribution || {}) 
          : (kwObj.date_distribution || {});
        total += dist[label] || 0;
      });
      labelTotals[label] = total;
    });

    // 3. 构建 100% 堆叠条形图所需的 datasets 占比数据
    const colors = getPalette(topKeywords.length);
    datasets = topKeywords.map((kwObj, index) => {
      const dist = (dimension === 'category') 
        ? (kwObj.category_distribution || {}) 
        : (kwObj.date_distribution || {});

      const data = labels.map(label => {
        const total = labelTotals[label] || 0;
        const val = dist[label] || 0;
        if (total === 0) return 0;
        return parseFloat(((val / total) * 100).toFixed(1));
      });

      return {
        label: kwObj.keyword,
        data: data,
        backgroundColor: colors[index],
        borderWidth: 0
      };
    });

    // 4. 动态调整 Canvas 容器高度以适应不同长度的 labels
    const container = canvas.parentElement;
    if (container) {
      const calculatedHeight = Math.max(450, labels.length * 38 + 120);
      container.style.height = `${calculatedHeight}px`;
    }

    const ctx = canvas.getContext('2d');
    
    keywordChartInstance = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: datasets
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: {
              color: '#1e293b',
              boxWidth: 12,
              font: {
                size: 14,
                weight: '500'
              }
            }
          },
          tooltip: {
            backgroundColor: 'rgba(15, 23, 42, 0.9)',
            titleFont: { size: 13, weight: 'bold' },
            bodyFont: { size: 12 },
            padding: 10,
            cornerRadius: 6,
            mode: 'nearest',
            intersect: true,
            axis: 'xy',
            callbacks: {
              label: function(context) {
                let label = context.dataset.label || '';
                if (label) {
                  label += ': ';
                }
                if (context.parsed.x !== null) {
                  label += context.parsed.x + '%';
                }
                return label;
              }
            }
          }
        },
        scales: {
          x: {
            stacked: true,
            max: 100, // 固定最大值为 100%
            grid: {
              color: 'rgba(226, 232, 240, 0.1)',
              borderColor: 'rgba(226, 232, 240, 0.2)'
            },
            ticks: {
              color: '#475569',
              font: { size: 13 },
              callback: function(value) {
                return value + '%'; // 刻度显示百分比后缀
              }
            }
          },
          y: {
            stacked: true,
            grid: {
              display: false
            },
            ticks: {
              color: '#1e293b',
              font: { size: 14, weight: '500' }
            }
          }
        }
      }
    });
  } catch (err) {
    console.error('drawDistributionChart error:', err);
    const canvas = document.getElementById('keywordDistributionChart');
    if (canvas && canvas.parentElement) {
      canvas.parentElement.innerHTML = `
        <div style="color: #ef4444; padding: 20px; border: 1px dashed #fca5a5; background-color: #fef2f2; border-radius: 8px; font-family: system-ui, sans-serif; margin-bottom: 20px;">
          <h3 style="margin-top: 0; display: flex; align-items: center; gap: 8px;">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"></path>
            </svg>
            图表渲染失败 / Chart Render Error
          </h3>
          <p style="font-weight: 600; margin-bottom: 8px;">${err.message}</p>
          <pre style="margin: 0; padding: 12px; background: #fee2e2; border-radius: 4px; font-size: 12px; overflow-x: auto; white-space: pre-wrap; color: #991b1b;">${err.stack}</pre>
        </div>
      `;
    }
  }
}

function drawTrendChart(trendData, validDatesInRange) {
  const chartElement = document.getElementById('trendChart');
  if (!chartElement) return;

  if (typeof d3 === 'undefined') {
    console.error('D3 is not defined, skipping trend chart rendering.');
    chartElement.innerHTML = '<p class="no-data" style="text-align: center; padding: 20px;">图表库 (D3) 加载失败，无法渲染趋势图 / Chart library failed to load.</p>';
    return;
  }

  const margin = {top: 20, right: 180, bottom: 80, left: 60};
  const width = Math.max(100, chartElement.offsetWidth - margin.left - margin.right);
  const height = 400 - margin.top - margin.bottom;

  // 清除旧的 SVG
  chartElement.innerHTML = '';

  const svg = d3.select('#trendChart')
    .append('svg')
      .attr('width', width + margin.left + margin.right)
      .attr('height', height + margin.top + margin.bottom)
    .append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

  // 设置比例尺
  const x = d3.scaleTime()
    .domain(d3.extent(validDatesInRange, d => new Date(d + 'T00:00:00Z')))
    .range([0, width]);

  const maxY = d3.max(trendData, d => d3.max(d.values, v => v.count)) || 1;
  const y = d3.scaleLinear()
    .domain([0, maxY])
    .nice()
    .range([height, 0]);

  // 创建颜色比例尺，使用更柔和的颜色
  const color = d3.scaleOrdinal()
    .range(['#4e79a7', '#f28e2c', '#59a14f', '#e15759', '#76b7b2', 
            '#edc949', '#af7aa1', '#ff9da7', '#9c755f', '#bab0ab']);

  // 提取 X 轴刻度，避免重复日期
  const tickValues = [];
  const step = Math.max(1, Math.ceil(validDatesInRange.length / 8));
  for (let i = 0; i < validDatesInRange.length; i += step) {
    tickValues.push(new Date(validDatesInRange[i] + 'T00:00:00Z'));
  }

  // 添加X轴网格线
  svg.append('g')
    .attr('class', 'grid')
    .attr('transform', `translate(0,${height})`)
    .style('stroke-dasharray', '3,3')
    .style('opacity', 0.1)
    .call(d3.axisBottom(x)
      .tickValues(tickValues)
      .tickSize(-height)
      .tickFormat(''));

  // 添加Y轴网格线
  svg.append('g')
    .attr('class', 'grid')
    .style('stroke-dasharray', '3,3')
    .style('opacity', 0.1)
    .call(d3.axisLeft(y)
      .tickSize(-width)
      .tickFormat(''));

  // 确定合适的日期格式
  function determineDateFormat(dates) {
    const startDate = new Date(dates[0] + 'T00:00:00Z');
    const endDate = new Date(dates[dates.length - 1] + 'T00:00:00Z');
    
    const sameYear = startDate.getFullYear() === endDate.getFullYear();
    const sameMonth = sameYear && startDate.getMonth() === endDate.getMonth();
    
    if (sameMonth) {
      return d3.timeFormat("%d");
    } else if (sameYear) {
      return d3.timeFormat("%m-%d");
    } else {
      return d3.timeFormat("%Y-%m-%d");
    }
  }

  const dateFormat = determineDateFormat(validDatesInRange);
  
  // 添加X轴
  svg.append('g')
    .attr('class', 'x-axis')
    .attr('transform', `translate(0,${height})`)
    .call(d3.axisBottom(x)
      .tickValues(tickValues)
      .tickFormat(dateFormat))
    .selectAll("text")
    .style("text-anchor", "end")
    .style("font-size", "11px")
    .style("fill", "#666")
    .attr("dx", "-.8em")
    .attr("dy", ".15em")
    .attr("transform", "rotate(-45)");

  // 添加Y轴
  svg.append('g')
    .attr('class', 'y-axis')
    .call(d3.axisLeft(y)
      .ticks(5))
    .selectAll("text")
    .style("font-size", "11px")
    .style("fill", "#666");

  // 添加Y轴标题
  svg.append("text")
    .attr("transform", "rotate(-90)")
    .attr("y", 0 - margin.left)
    .attr("x", 0 - (height / 2))
    .attr("dy", "1em")
    .style("text-anchor", "middle")
    .style("fill", "#666")
    .style("font-size", "11px")
    .text("出现频次 (Frequency)");

  // 添加X轴标题
  const latestDate = new Date(validDatesInRange[0] + 'T00:00:00Z');
  const earliestDate = new Date(validDatesInRange[validDatesInRange.length - 1] + 'T00:00:00Z');
  let xAxisTitle = "";
  
  if (latestDate.getFullYear() === earliestDate.getFullYear()) {
    if (latestDate.getMonth() === earliestDate.getMonth()) {
      xAxisTitle = `${latestDate.getFullYear()}/${String(latestDate.getMonth() + 1).padStart(2, '0')}`;
    } else {
      xAxisTitle = `${latestDate.getFullYear()}`;
    }
  }
  
  if (xAxisTitle) {
    svg.append("text")
      .attr("transform", `translate(${width/2}, ${height + margin.bottom - 5})`)
      .style("text-anchor", "middle")
      .style("fill", "#666")
      .style("font-size", "12px")
      .text(xAxisTitle);
  }

  svg.selectAll('.x-axis path, .y-axis path, .x-axis line, .y-axis line')
    .style('stroke', '#ccc')
    .style('stroke-width', '1px');

  // 定义面积生成器
  const area = d3.area()
    .x(d => x(d.date))
    .y0(height)
    .y1(d => y(d.count))
    .curve(d3.curveMonotoneX);

  // 定义线条生成器
  const line = d3.line()
    .x(d => x(d.date))
    .y(d => y(d.count))
    .curve(d3.curveMonotoneX);

  // 添加渐变定义
  const gradient = svg.append("defs")
    .selectAll("linearGradient")
    .data(trendData)
    .enter()
    .append("linearGradient")
    .attr("id", (d, i) => `gradient-${i}`)
    .attr("x1", "0%")
    .attr("y1", "0%")
    .attr("x2", "0%")
    .attr("y2", "100%");

  gradient.append("stop")
    .attr("offset", "0%")
    .attr("stop-color", d => color(d.keyword))
    .attr("stop-opacity", 0.25);

  gradient.append("stop")
    .attr("offset", "100%")
    .attr("stop-color", d => color(d.keyword))
    .attr("stop-opacity", 0.01);

  // 绘制面积
  const areas = svg.selectAll('.area')
    .data(trendData)
    .enter()
    .append('path')
      .attr('class', 'area')
      .attr('d', d => area(d.values))
      .style('fill', (d, i) => `url(#gradient-${i})`)
      .style('opacity', 0.6);

  // 绘制折线
  const paths = svg.selectAll('.line')
    .data(trendData)
    .enter()
    .append('path')
      .attr('class', 'line')
      .attr('d', d => line(d.values))
      .style('stroke', d => color(d.keyword))
      .style('fill', 'none')
      .style('stroke-width', 2.5)
      .style('opacity', 0.85);

  // 绘制折线节点圆点，增加细节感
  const dotsG = svg.append('g').attr('class', 'dots-group');
  trendData.forEach((d, i) => {
    dotsG.selectAll(`.dot-${i}`)
      .data(d.values)
      .enter()
      .append('circle')
        .attr('class', `dot dot-${i}`)
        .attr('cx', v => x(v.date))
        .attr('cy', v => y(v.count))
        .attr('r', 3)
        .style('fill', color(d.keyword))
        .style('opacity', 0)
        .style('transition', 'opacity 0.2s ease');
  });

  // 添加图例
  const legend = svg.selectAll('.legend')
    .data(trendData)
    .enter()
    .append('g')
      .attr('class', 'legend')
      .attr('transform', (d, i) => `translate(${width + 20},${i * 24})`);

  legend.append('rect')
    .attr('x', 0)
    .attr('width', 16)
    .attr('height', 16)
    .attr('rx', 3)
    .style('fill', d => color(d.keyword))
    .style('opacity', 0.8);

  legend.append('text')
    .attr('x', 24)
    .attr('y', 11)
    .text(d => d.keyword)
    .style('font-size', '12px')
    .style('font-weight', '500')
    .style('alignment-baseline', 'middle')
    .style('fill', '#333');

  // 添加交互效果
  legend.style('cursor', 'pointer')
    .on('mouseover', function(event, d) {
      const keyword = d.keyword;
      const targetIndex = trendData.findIndex(item => item.keyword === keyword);
      
      areas.style('opacity', 0.05);
      paths.style('opacity', 0.1);
      svg.selectAll('.dot').style('opacity', 0);
      
      svg.selectAll('.area')
        .filter(p => p.keyword === keyword)
        .style('opacity', 0.85);
      
      svg.selectAll('.line')
        .filter(p => p.keyword === keyword)
        .style('opacity', 1)
        .style('stroke-width', 3.5);

      svg.selectAll(`.dot-${targetIndex}`)
        .style('opacity', 1)
        .attr('r', 4.5);
    })
    .on('mouseout', function() {
      areas.style('opacity', 0.6);
      paths.style('opacity', 0.85).style('stroke-width', 2.5);
      svg.selectAll('.dot').style('opacity', 0).attr('r', 3);
    });
}



function formatDate(dateString) {
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric'
  });
}

// 修改 showRelatedPapers 函数中生成论文卡片的部分
function showRelatedPapers(keyword) {
    const sidebar = document.getElementById('paperSidebar');
    const selectedKeywordElement = document.getElementById('selectedKeyword');
    const relatedPapersContainer = document.getElementById('relatedPapers');
    
    // 更新关键词显示
    selectedKeywordElement.textContent = 'Keyword: ' + keyword;
    
    // 查找包含关键词的论文
    const relatedPapers = allPapersData.filter(paper => {
        const searchText = (paper.title + ' ' + paper.summary).toLowerCase();
        return searchText.includes(keyword.toLowerCase());
    });
    
    // 生成相关论文的HTML
    const papersHTML = relatedPapers.map((paper, index) => `
        <div class="paper-card">
            <div class="paper-number">${index + 1}</div>
            <a href="${paper.url}" target="_blank" class="paper-title">
                ${paper.title}
                ${paper.translated_title && paper.translated_title !== paper.title ? `<div class="paper-title-zh">${escapeHtml(paper.translated_title)}</div>` : ''}
            </a>
            <div class="paper-authors">${paper.authors}</div>
            <div class="paper-categories">
                ${paper.category.map(cat => `<span class="category-tag">${cat}</span>`).join('')}
            </div>
            <div class="paper-summary">${paper.summary}</div>
        </div>
    `).join('');
    
    // 更新侧边栏内容
    relatedPapersContainer.innerHTML = relatedPapers.length > 0 
        ? papersHTML 
        : '<p>No related papers found.</p>';
    
    // 显示侧边栏
    sidebar.classList.add('active');
}

// 添加新函数：关闭侧边栏
function closeSidebar() {
  const sidebar = document.getElementById('paperSidebar');
  sidebar.classList.remove('active');
}

function buildNetworkData(papers, topN = 30) {
    const keywordFreq = {};
    (papers || []).forEach(p => {
        const kws = Array.isArray(p.keywords) ? p.keywords : extractKeywords(p.title || '');
        if (!kws || kws.length === 0) return;
        const uniqueKeywords = Array.from(new Set(kws));
        uniqueKeywords.forEach(k => {
            keywordFreq[k] = (keywordFreq[k] || 0) + 1;
        });
    });

    const sortedKeywords = Object.keys(keywordFreq)
        .sort((a, b) => keywordFreq[b] - keywordFreq[a])
        .slice(0, topN);
    
    const validKeywordsSet = new Set(sortedKeywords);
    
    const nodes = sortedKeywords.map(k => ({ id: k, value: keywordFreq[k] }));
    const linkMap = {};

    (papers || []).forEach(p => {
        const kws = Array.isArray(p.keywords) ? p.keywords : extractKeywords(p.title || '');
        if (!kws || kws.length === 0) return;
        const uniqueKeywords = Array.from(new Set(kws));
        const validKws = uniqueKeywords.filter(k => validKeywordsSet.has(k));
        for (let i = 0; i < validKws.length; i++) {
            for (let j = i + 1; j < validKws.length; j++) {
                const k1 = validKws[i];
                const k2 = validKws[j];
                const source = k1 < k2 ? k1 : k2;
                const target = k1 < k2 ? k2 : k1;
                const key = JSON.stringify([source, target]);
                if (!linkMap[key]) {
                    linkMap[key] = { source, target, value: 0 };
                }
                linkMap[key].value += 1;
            }
        }
    });

    const links = Object.values(linkMap);

    return { nodes, links };
}

function renderNetwork(dataOrPapers) {
    const container = document.getElementById('networkContainer');
    if (!container) return;
    container.innerHTML = '';
    
    let nodes, links;
    if (dataOrPapers && dataOrPapers.nodes && dataOrPapers.links) {
        nodes = dataOrPapers.nodes;
        links = dataOrPapers.links;
    } else {
        const net = buildNetworkData(dataOrPapers, 35);
        nodes = net.nodes;
        links = net.links;
    }
    
    if (!nodes || nodes.length === 0) {
        container.innerHTML = '<div style="padding:20px;text-align:center;">No sufficient keyword data found.</div>';
        return;
    }

    const width = container.clientWidth || 800;
    const height = container.clientHeight || 500;
    
    // Setup SVG and Tooltip
    const tooltip = d3.select(container).append("div")
        .attr("class", "network-tooltip");

    const svg = d3.select(container).append("svg")
        .attr("width", width)
        .attr("height", height);

    const g = svg.append("g");

    // Zoom
    svg.call(d3.zoom()
        .scaleExtent([0.1, 4])
        .on("zoom", (event) => {
            g.attr("transform", event.transform);
        }));

    // Color scale using the unified theme palette
    const themeColors = [
      '#6366f1', '#10b981', '#f43f5e', '#eab308', '#3b82f6', 
      '#ec4899', '#8b5cf6', '#14b8a6', '#f97316', '#a855f7',
      '#06b6d4', '#84cc16', '#22c55e', '#ef4444', '#0284c7'
    ];
    const color = d3.scaleOrdinal(themeColors);
    
    // Size scale
    const sizeScale = d3.scaleSqrt()
        .domain([d3.min(nodes, d => d.value) || 1, d3.max(nodes, d => d.value) || 10])
        .range([5, 20]);

    // Link width scale to make strength visually distinguishable
    const linkWidthScale = d3.scaleSqrt()
        .domain([d3.min(links, d => d.value) || 1, d3.max(links, d => d.value) || 5])
        .range([1, 8]);

    // Force simulation
    const simulation = d3.forceSimulation(nodes)
        .force("link", d3.forceLink(links).id(d => d.id).distance(100))
        .force("charge", d3.forceManyBody().strength(-200))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("collide", d3.forceCollide().radius(d => sizeScale(d.value) + 10));

    // Links
    const link = g.append("g")
        .attr("class", "network-links")
        .selectAll("line")
        .data(links)
        .enter().append("line")
        .attr("class", "network-link")
        .attr("stroke-width", d => linkWidthScale(d.value));

    // Nodes
    const node = g.append("g")
        .attr("class", "network-nodes")
        .selectAll("circle")
        .data(nodes)
        .enter().append("circle")
        .attr("class", "network-node")
        .attr("r", d => sizeScale(d.value))
        .attr("fill", d => color(d.id))
        .call(d3.drag()
            .on("start", dragstarted)
            .on("drag", dragged)
            .on("end", dragended));

    // Labels
    const label = g.append("g")
        .attr("class", "network-labels")
        .selectAll("text")
        .data(nodes)
        .enter().append("text")
        .attr("class", "network-label")
        .text(d => d.id)
        .attr("x", 8)
        .attr("y", "0.31em");

    // Interactivity
    node.on("mouseover", (event, d) => {
        // Dim others
        node.style("opacity", o => isConnected(d, o) ? 1 : 0.1);
        link.style("stroke-opacity", o => (o.source === d || o.target === d ? 1 : 0.1));
        label.style("opacity", o => isConnected(d, o) ? 1 : 0.1);
        
        tooltip.transition().duration(200).style("opacity", 1);
        tooltip.html(`<b>${d.id}</b><br/>Freq: ${d.value}`)
            .style("left", (event.pageX + 10) + "px")
            .style("top", (event.pageY - 28) + "px");
    }).on("mouseout", () => {
        node.style("opacity", 1);
        link.style("stroke-opacity", 0.8);
        label.style("opacity", 1);
        tooltip.transition().duration(500).style("opacity", 0);
    }).on("click", (event, d) => {
        // Reuse sidebar logic
        if (typeof showRelatedPapers === 'function') {
            showRelatedPapers(d.id);
        }
    });

    const linkedByIndex = {};
    links.forEach(d => {
        linkedByIndex[`${d.source.id || d.source},${d.target.id || d.target}`] = 1;
    });

    function isConnected(a, b) {
        return linkedByIndex[`${a.id},${b.id}`] || linkedByIndex[`${b.id},${a.id}`] || a.id === b.id;
    }

    simulation.on("tick", () => {
        link
            .attr("x1", d => d.source.x)
            .attr("y1", d => d.source.y)
            .attr("x2", d => d.target.x)
            .attr("y2", d => d.target.y);
        node
            .attr("cx", d => d.x)
            .attr("cy", d => d.y);
        label
            .attr("x", d => d.x + sizeScale(d.value) + 2)
            .attr("y", d => d.y + 3);
    });

    function dragstarted(event, d) {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
    }
    function dragged(event, d) {
        d.fx = event.x;
        d.fy = event.y;
    }
    function dragended(event, d) {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
    }
}