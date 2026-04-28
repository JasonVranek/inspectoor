// Shared mutable state -- single source of truth for the app
export const state = {
  catalog: null,
  allItems: [],
  allEndpoints: [],
  searchQuery: '',
  typeFilters: { spec: '', kind: '', domain: '', fork: '' },
  epFilters: { spec: '', method: '', domain: '', group: '' },
};
