try{const response=await fetch('http://127.0.0.1:8787/v1/health',{signal:AbortSignal.timeout(4000)});if(!response.ok||(await response.json()).ok!==true)process.exit(1)}catch{process.exit(1)}
