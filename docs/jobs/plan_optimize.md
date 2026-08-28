
You are a senior software engineer with a scientific persona that must verify and test everything and ground all solutions based on skills, mcps and web searches.
Never trust your memory, for example don't use specific lib versions, there are alot newer versions with newer features and you must learn them first.
Don't search for specific terms like, make your searches general. For example, don't search "Latest gemini flash 2.5 endpoint", instead search "Latest gemini flash version name and reference", because now there is gemini 3.7 flash and more coming soon.

You always answer based on the most recent packages, features, approaches and must always catch and call out "out-dated" approaches or bad logic without bias based on grounded searches.

You must must always ask for more details when needed and you must not make assumptions.

You Must always reject any ad-hoc logic or flow that leads to redundancy, extra cost, latency, or worse performance. 
If there is code that does alot of things manually or created from scratch and there exists libs that can replace that logic reliabliby, then we must switch to the reliable lib to avoid brittle reinvention of the wheel.

You must always be bold, objective and grounded.

You must always lean in decisions towards simplifying not complicating.

You must stop to ask questions when a major decision is to be made like choosing a framework/lib/ specific model name ,..etc.


Check recent two session from the user db, analyze them in detail. Spawn subagents to help you veirfy all points. you must iterate to discover, fix and retest adhering to rules in AGENTS.md


Here is a rough initial report from another agent:   Forensic Database & Behavioral Harness Audit (Sessions 150 & 151)                            
                                                                                                
  An in-depth forensic audit of the SQLite database at chat_sessions.db was conducted across the
  sessions, runs, events, snapshots, and tool_effects tables, evaluating agent behavior against 
  context (prompts, tool schemas, and returns) guided by AGENTS.md.                             
  ──────                                                                                        
  ## 1. Session Breakdown & Overview                                                            
                                                                                                
   Dimension         │ Session 150                        │ Session 151
  ───────────────────┼────────────────────────────────────┼─────────────────────────────────────
   User Prompt       │ "make me a phase locked loop       │ "Explain what signal processing
                     │ system with explanations"          │ pipeline this flowgraph
                     │                                    │ implements."
   Model / Provider  │ glm-5.3-flash:cloud (ollama_cloud) │ glm-5.3-flash:cloud (ollama_cloud)
   Flowgraph         │ untitled:untitled.grc (0 blocks,   │ playground/experiment_read_files/as
                     │ empty canvas)                      │ k.grc (34 blocks, 25 connections)
   Duration & Result │ ~18 min — Aborted / Interrupted by │ ~1 min 28s — Complete & Accurate
                     │ User                               │
   Tool Usage        │ 13 tool calls: inspect_graph, 8×   │ 6 tool calls: inspect_graph, 2×
                     │ query_knowledge, 3× web_fetch, 3×  │ query_knowledge, 4× web_fetch (Wiki
                     │ run_command, write_file,           │ 403, Docs DNS fail, 2× GitHub raw
                     │ change_graph                       │ C++ source)
   Context Size      │ 36,596 max input tokens            │ 26,384 max input tokens
  ──────                                                                                        
  ## 2. Key Forensic Findings: Behavior vs. Harness Context                                     
                                                                                                
  ### 🚨 1. Prompt Injection Defender False-Positives on Official Documentation                 
                                                                                                
  • Evidence (Session 150, Turn 4): When the agent attempted to ground PLL parameter units via  
  web_fetch on official Doxygen documentation (https://www.gnuradio.                            
  org/doc/doxygen/classgr_1_1analog_1_1pll__freqdet__cf.html), the harness intercepted and      
  suppressed the response:                                                                      
    "content": "The result of `web_fetch` was withheld: it matched prompt injection patterns    
  (risk: high). Detections: ['shell_command']"                                                  
                                                                                                
  • Impact: The agent was blinded from reading official documentation because C++/Python        
  docstrings contained words matching generic shell command regexes. This directly forced the   
  agent into catastrophic fallback paths (attempting to execute Python work functions in        
  subshells, triggering segmentation faults, and hanging in deep shell searches).               
  • AGENTS.md Invariant: Violates Maximizing Context & No String-Based Clipping. Technical      
  documentation must not be blocked by over-aggressive heuristic filters.                       
  ──────                                                                                        
  ### 🔍 2. Local Vector RAG (query_knowledge) Parameter Detail Gap                             
                                                                                                
  • Evidence: In both sessions, querying the local RAG corpus for block implementation semantics
  (e.g. analog_pll_carriertracking_cc parameters, keep_m_in_n offset math, or fec_ber_bf output 
  rather than parameter unit definitions (e.g., w in radians/sample).                           
  • Impact: The model was forced to rely on external web fetches (which hit 403s on wiki.       
  scale) returned generic high-level coding guides (BlocksCodingGuide, Sample_Rate_Tutorial)    
  gnuradio.org and defender blocks on doxygen) or raw GitHub C++ source reads.                  
  ──────                                                                                        
  ### ⏱️ 3. 600s Shell Timeout on Discovery Commands                                            
                                                                                                
  • Evidence (Session 150, Turn 6): The agent ran a broad discovery command (find $GNDIR/..     
  /share/...) that hung for 600.0 seconds (10 minutes) before GrcShellToolset timed out.        
  • Impact: The user experienced a 10-minute freeze with no progress feedback, prompting user   
  frustration ("?" and "make the grc now!!!").                                                  
  ──────                                                                                        
  ### 📐 4. GRC Evaluation Scope (math.pi) & Sink Multiplicity                                  
                                                                                                
  • Evidence (Session 150, Turn 8): When change_graph was finally invoked, it failed on 17      
  validation errors:                                                                            
      1. 14× name 'pi' is not defined: Using unprefixed pi instead of math.pi in parameter      
      expressions (2*pi*loop_bw/samp_rate).                                                     
      2. 3× Port is not connected: Declaring nconnections=3 on qtgui_time_sink_x while only     
      wiring ports 0 and 1.                                                                     
  • Prompt Evolution Note: The system prompt in prompts.py has since been updated with explicit 
  math. namespace guidance and anti-procrastination rules ("proceed directly to change_graph    
  without writing scratch scripts").                                                            
  ──────                                                                                        
  ### 🌟 5. High-Precision DSP Analysis in Session 151                                          
                                                                                                
  • Evidence: In Session 151, the agent parsed the ASK/OOK transceiver flowgraph, accurately    
  identified coherent demodulation and FIR group delay, and proved a mathematical flaw in the   
  reference-path decimation (keep_m_in_n stride 200 mod 64 = 8, causing a shifting 8-symbol     
  alignment error and ~98% synthetic BER) by fetching and reading the raw C++ block source from 
  GitHub.
  ──────
  ## 3. Recommended Harness Optimizations
  
  1. Allowlist Official Documentation in PromptInjectionDefender:
      • Exempt official domains (e.g., *.gnuradio.org, raw.githubusercontent.com/gnuradio) or   
      tune shell-command pattern heuristics so standard C++ API docstrings are not flagged as   
      high-risk injections.
  2. Enrich Local Vector Corpus (ingest.py):
      • Index GNU Radio Doxygen class references, block YAML definitions, and parameter units   
      directly into the local SQLite vector database (chat_knowledge.db), allowing the model to 
      ground block parameters offline without web calls.
  3. Adaptive Shell Command Timeout:
      • Distinguish between long compilation/simulations (600s) and discovery commands (find,   
      grep, python3 -c ...), applying a tighter 15–30s timeout on inspection commands.          
  4. Intermediate Mutation Guidance:
      • Remind models to use force=True on intermediate turns when multi-turn edits temporarily 
      leave declared multi-sink ports (nconnections > 1) pending connection.
