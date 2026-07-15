I've been cooking something at Qoherent that I think is genuinely useful for the wireless and SDR community.

The GRC Agent is an autonomous AI that lives inside GNU Radio Companion. It can see your flowgraphs, understand what they do, and edit them directly -- adding blocks, updating parameters, rewiring connections, even running high-level projects on its own. The design is what makes it work: we built a smart harness around it with a carefully defined set of tools so the agent operates efficiently instead of guessing its way through the block tree.

What this means in practice is that wireless communication engineers can speed through repetitive setup work, and people without a deep comms background can still build and experiment with flowgraphs that would normally be out of reach. The agent handles the wiring so you can focus on what you actually want to build.

Early testing has been incredibly promising, and we're building this in the open. I'd love to hear what you think -- what would an AI agent need to do to earn a spot in your SDR workflow?

github.com/qoherent/grc-agent
