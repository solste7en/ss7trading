export let currentTab = 'positions';

export const overviewState = { loaded: false };
export const OVERVIEW_LIMIT = 10;

export const customTickerState = { symbol: '', page: 1, pages: 1, total: 0 };

export const POSITION_LIST_OTHER_ID = 4;

export let _posData = [];
export let _posLists = [];
export let _posAssignments = {};
export let _posRecentMetrics = {};
export let _posSortCol = null;
export let _posSortDir = 1;
export let _posExpanded = new Set();
export let _posDndBound = false;
export let _posVolume365 = {};
export let _posDndPayload = null;

export let _posChartLong = null;
export let _posChartShort = null;
export let _posOthersMiniChart = null;
export let _posOthersHideTimer = null;
export let _posOthersPopoverListeners = false;

export const incomePnlState = { page: 1, loaded: false };
export const _ipExpanded = new Set();
export let _ipCardFilter = null;
export const _ipRecoveryCache = {};
export let incomePnlSort = { key: 'open_date', dir: 'desc' };

export let tradeMode = 'equity';
export let _pendingOrder = null;
export let _tradeTicker = '';
export let _tradeQuoteData = null;

export let _chainData = null;

export let ladderRungs = [{qty:'', price:''}, {qty:'', price:''}];
export let _ladderSubmitting = false;

export let _lastSuggestTicker = '';

export let ladderSuggestion = undefined;

export let _verifyOpen = false;

export const ladderRecentState = { page: 1, pages: 1, total: 0, eqCount: null, optCount: null };
export const LADDER_RECENT_LIMIT = 20;
export const LADDER_RECENT_ACTIONS = new Set([
  'Buy','Sell','Sell Short','Buy to Cover',
  'Buy to Open','Sell to Open','Buy to Close','Sell to Close'
]);

export let _ladderOrders = [];

export let _allOrders = [];
export let _ordSortCol = 'entered_time', _ordSortDir = -1;
export const ordersState = { loaded: false };

export let stratMode = 'naked';
export let _stratTicker = '';
export let _stratChainData = null;
export let _stratPendingOrder = null;
export let _stratSuggestions = [];
export let _stratOrders = [];

export const STRAT_MODES = ['naked', 'vertical', 'collar', 'bundle'];

export const CHAIN_PAGE_SIZE = 20;
export let _chainAllStrikes = [];
export let _chainCallMap    = {};
export let _chainPutMap     = {};
export let _chainPageIdx    = 0;
export let _chainVisibleStrikes = [];

export const wlState = { lists: [], currentId: 'positions', initialized: false };
export const historyState = { page:1, loaded:false };
export const gainsState = { page:1, loaded:false };
export const _paginationRegistry = {};
export const stratRecentState = { page: 1 };
