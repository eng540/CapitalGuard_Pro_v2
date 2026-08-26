import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/NotFound";
import { Route, Switch } from "wouter";
import ErrorBoundary from "./components/ErrorBoundary";
import TmaBridge from "./components/TmaBridge";
import { ThemeProvider } from "./contexts/ThemeContext";
import Home from "./pages/Home";
import Workspace from "./pages/Workspace";
import Recommendations from "./pages/Recommendations";
import Analysts from "./pages/Analysts";
import AnalystDossier from "./pages/AnalystDossier";
import SignalDiscovery from "./pages/SignalDiscovery";
import Historical from "./pages/Historical";
import RiskStudio from "./pages/RiskStudio";
import Admin from "./pages/Admin";
import SmartDropzone from "./pages/SmartDropzone";
import AnalystWorkspace from "./pages/AnalystWorkspace";
import StudioHub from "./pages/StudioHub";

function Router() {
  // make sure to consider if you need authentication for certain routes
  return (
    <Switch>
      <Route path={"/"} component={Home} />
      <Route path={"/app"} component={SmartDropzone} />
      <Route path={"/radar"} component={SmartDropzone} />
      <Route path={"/portfolio"} component={Workspace} />
      <Route path={"/studio"} component={StudioHub} />
      <Route path={"/recommendations"} component={Recommendations} />
      <Route path={"/analysts"} component={Analysts} />
      <Route path={"/analysts/:code"} component={AnalystDossier} />
      <Route path={"/signals"} component={SignalDiscovery} />
      <Route path={"/analyst/workspace"} component={AnalystWorkspace} />
      <Route path={"/historical"} component={Historical} />
      <Route path={"/risk"} component={RiskStudio} />
      <Route path={"/admin"} component={Admin} />
      <Route path={"/smart"} component={SmartDropzone} />
      <Route path={"/404"} component={NotFound} />
      {/* Final fallback route */}
      <Route component={NotFound} />
    </Switch>
  );
}

// NOTE: About Theme
// - First choose a default theme according to your design style (dark or light bg), than change color palette in index.css
//   to keep consistent foreground/background color across components
// - If you want to make theme switchable, pass `switchable` ThemeProvider and use `useTheme` hook

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider
        defaultTheme="dark"
        // switchable
      >
        <TooltipProvider>
          <TmaBridge />
          <Toaster />
          <Router />
        </TooltipProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
