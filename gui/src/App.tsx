import { useEffect, useState } from "react";
import { core, setCorePath } from "./core";

export default function App() {
  const [out, setOut] = useState<string>("");
  useEffect(() => { setCorePath(null); }, []);
  const run = async () => {
    try { setOut(JSON.stringify(await core("status"), null, 2)); }
    catch (e) { setOut(String(e)); }
  };
  return (
    <div>
      <h1>vision-relay GUI</h1>
      <button onClick={run}>status</button>
      <pre>{out}</pre>
    </div>
  );
}
