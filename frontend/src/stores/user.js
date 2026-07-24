import { createUserStore } from "ling-yun-methods";
import { defineStore } from "pinia";

export const useUserStore = createUserStore({
  prefixUrl: import.meta.env.VITE_API_URL_PREFIX,
  defineStore,
})();
