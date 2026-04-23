/**
 * Single mutable app state object.
 * ES module namespace imports are read-only; `import * as S` then `S._posData = x` throws.
 * All shared state lives on `store` so modules can do `store._posData = x`.
 */
export const store = {
  currentTab: 'positions',

  overviewState: { loaded: false },
  balanceState: { loaded: false },
  OVERVIEW_LIMIT: 10,

  customTickerState: { symbol: '', page: 1, pages: 1, total: 0 },

  POSITION_LIST_OTHER_ID: 4,

  _posData: [],
  _posLists: [],
  _posAssignments: {},
  _posRecentMetrics: {},
  _posSortCol: null,
  _posSortDir: 1,
  _posExpanded: new Set(),
  _posDndBound: false,
  _posVolume365: {},
  /** Uppercase symbol -> next earnings YYYY-MM-DD or null (from /api/earnings). */
  _posEarnings: {},
  _posDndPayload: null,

  _posChartLong: null,
  _posChartShort: null,
  _posOthersMiniChart: null,
  _posOthersHideTimer: null,
  _posOthersPopoverListeners: false,

  incomePnlState: { page: 1, loaded: false },
  _ipExpanded: new Set(),
  _ipCardFilter: null,
  _ipRecoveryCache: {},
  incomePnlSort: { key: 'open_date', dir: 'desc' },

  tradeMode: 'equity',
  _pendingOrder: null,
  _tradeTicker: '',
  _tradeQuoteData: null,

  _chainData: null,
  /** Trade-tab option chain maps (full expiry slice; strike dropdown + table stay aligned). */
  _tradeChainCallMap: {},
  _tradeChainPutMap: {},
  _tradeChainAllStrikes: [],

  ladderRungs: [{ qty: '', price: '' }, { qty: '', price: '' }],
  _ladderSubmitting: false,

  _lastSuggestTicker: '',

  ladderSuggestion: undefined,

  _verifyOpen: false,

  ladderRecentState: { page: 1, pages: 1, total: 0, eqCount: null, optCount: null },
  LADDER_RECENT_LIMIT: 20,
  LADDER_RECENT_ACTIONS: new Set([
    'Buy', 'Sell', 'Sell Short', 'Buy to Cover',
    'Buy to Open', 'Sell to Open', 'Buy to Close', 'Sell to Close',
  ]),

  _ladderOrders: [],

  _allOrders: [],
  _ordersCtx: { quoteBySymbol: {}, earningsByUnderlying: {} },
  _ordSortCol: 'entered_time',
  _ordSortDir: -1,
  ordersState: { loaded: false },

  stratMode: 'naked',
  _stratTicker: '',
  _stratChainData: null,
  _stratPendingOrder: null,
  _stratSuggestions: [],
  _stratOrders: [],

  STRAT_MODES: ['naked', 'vertical', 'collar', 'bundle'],

  CHAIN_PAGE_SIZE: 20,
  _chainAllStrikes: [],
  _chainCallMap: {},
  _chainPutMap: {},
  _chainPageIdx: 0,
  _chainVisibleStrikes: [],

  analyticsState: { loaded: false },
  consolidationState: { loaded: false, overlapData: null },

  wlState: { lists: [], currentId: 'positions', initialized: false },
  /** Quote tab: last fetched rows + earnings map + client sort. */
  _qRows: [],
  _qEarnMap: {},
  _qSortCol: 'symbol',
  _qSortDir: 1,
  _qIsCustom: false,
  _qListId: 'positions',
  historyState: { page: 1, loaded: false },
  gainsState: { page: 1, loaded: false },
  _paginationRegistry: {},
  stratRecentState: { page: 1 },
};
