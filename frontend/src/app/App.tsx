import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GlobalErrorBoundary } from "@/app/GlobalErrorBoundary";
import { ThemeProvider } from "@/app/ThemeProvider";
import { AppRouter } from "@/routes/router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <GlobalErrorBoundary>
          <AppRouter />
        </GlobalErrorBoundary>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
