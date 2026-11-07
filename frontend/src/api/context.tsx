import { createContext, useContext } from "react";
import { ApiClient } from "./client";
export const ApiContext = createContext(new ApiClient());
export const useApi = () => useContext(ApiContext);
