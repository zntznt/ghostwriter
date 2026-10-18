-- Ghostwriter validator harness. Loaded as a scenario by validator/server.py;
-- Python calls it over RCON:
--   /sc remote.call("ghostwriter", "validate", "<blueprint string>")
-- and gets back JSON via rcon.print:
--   {import = rc, export = "<string>", builds = {[path] = build}}
-- rc is LuaItemStack.import_stack's result (0 ok, -1 ok with errors, 1 fail).
-- Each build covers one blueprint (path "" or "1/0" inside books):
--   {entities = n, unplaced = {...}, unplaceable = {...}, unrevived = {...},
--    rebuilt = "<string>"}
-- unplaceable: names that can't be placed even alone on the test surface
-- (surface conditions, water, rails); unplaced: the rest, i.e. the blueprint
-- itself collides.

local SURFACE = "ghostwriter"
local MARGIN = 8

local function surface()
  local s = game.surfaces[SURFACE]
  if not s then
    s = game.create_surface(SURFACE, {default_enable_all_autoplace_controls = false, no_enemies_mode = true})
  end
  return s
end

local function clear(s, area)
  for _, e in pairs(s.find_entities_filtered{area = area}) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
end

local function prepare(s, area)
  local lt, rb = area[1], area[2]
  s.request_to_generate_chunks({0, 0}, math.ceil(math.max(rb[1] - lt[1], rb[2] - lt[2]) / 64) + 1)
  s.force_generate_chunk_requests()
  clear(s, area)
  local tiles = {}
  for x = math.floor(lt[1]), math.ceil(rb[1]) do
    for y = math.floor(lt[2]), math.ceil(rb[2]) do
      tiles[#tiles + 1] = {name = "refined-concrete", position = {x, y}}
    end
  end
  s.set_tiles(tiles)
end

local function bounds(stack)
  local x1, y1, x2, y2 = 0, 0, 0, 0
  for _, e in pairs(stack.get_blueprint_entities() or {}) do
    x1, y1 = math.min(x1, e.position.x), math.min(y1, e.position.y)
    x2, y2 = math.max(x2, e.position.x), math.max(y2, e.position.y)
  end
  for _, t in pairs(stack.get_blueprint_tiles() or {}) do
    x1, y1 = math.min(x1, t.position.x), math.min(y1, t.position.y)
    x2, y2 = math.max(x2, t.position.x), math.max(y2, t.position.y)
  end
  -- The build is centred on {0,0}, so make the area symmetric and generous.
  local r = math.max(-x1, -y1, x2, y2) + MARGIN
  return {{-r, -r}, {r, r}}
end

local function key(name, x, y)
  return name .. "@" .. (math.floor(x * 8 + 0.5) / 8) .. "," .. (math.floor(y * 8 + 0.5) / 8)
end

-- Build a blueprint centred on {0,0} and revive it. Returns the report part
-- shared by validation and circuit tests, the area, and the built entities
-- keyed by their blueprint entity_number.
local function place(stack)
  local s = surface()
  local area = bounds(stack)
  prepare(s, area)
  local out = {entities = #(stack.get_blueprint_entities() or {}), unplaced = {}, unplaceable = {}, unrevived = {}}
  local blueprint_entities = stack.get_blueprint_entities() or {}

  local ghosts = stack.build_blueprint{
    surface = s, force = "player", position = {0, 0},
    build_mode = defines.build_mode.forced, skip_fog_of_war = false,
  }
  local placed = {}
  for _, g in pairs(ghosts) do
    if g.valid and g.name == "entity-ghost" then
      placed[g.ghost_name] = (placed[g.ghost_name] or 0) + 1
    end
  end
  local wanted = {}
  for _, e in pairs(blueprint_entities) do
    wanted[e.name] = (wanted[e.name] or 0) + 1
  end
  local probe = {area[2][1] + 2 * MARGIN, 0}
  for name, n in pairs(wanted) do
    local missing = n - (placed[name] or 0)
    if missing > 0 then
      prepare(s, {{probe[1] - MARGIN, -MARGIN}, {probe[1] + MARGIN, MARGIN}})
      local alone = s.can_place_entity{name = name, position = probe, force = "player",
                                       build_check_type = defines.build_check_type.manual}
      local list = alone and out.unplaced or out.unplaceable
      list[#list + 1] = name .. " x" .. missing
    end
  end

  -- build_blueprint shifts the blueprint to align it to the grid; find the
  -- shift by voting over same-named ghosts, then map ghosts to entity numbers.
  local votes, best, best_n = {}, {0, 0}, 0
  for _, g in pairs(ghosts) do
    if g.valid and g.name == "entity-ghost" then
      for _, e in pairs(blueprint_entities) do
        if e.name == g.ghost_name then
          local d = key("", g.position.x - e.position.x, g.position.y - e.position.y)
          votes[d] = (votes[d] or 0) + 1
          if votes[d] > best_n then best, best_n = {g.position.x - e.position.x, g.position.y - e.position.y}, votes[d] end
        end
      end
    end
  end
  local number_at = {}
  for _, e in pairs(blueprint_entities) do
    number_at[key(e.name, e.position.x + best[1], e.position.y + best[2])] = e.entity_number
  end

  -- Tile ghosts first so entities that need the tiles can revive.
  for _, g in pairs(ghosts) do
    if g.valid and g.name == "tile-ghost" then g.revive() end
  end
  local built = {}
  for _, g in pairs(ghosts) do
    if g.valid and g.name == "entity-ghost" then
      local name, number = g.ghost_name, number_at[key(g.ghost_name, g.position.x, g.position.y)]
      local _, entity = g.revive()
      if not entity then
        out.unrevived[#out.unrevived + 1] = name
      elseif number then
        built[number] = entity
      end
    end
  end
  return out, area, built
end

local function build(stack)
  local out, area = place(stack)
  local s = surface()
  local inv = game.create_inventory(1)
  local copy = inv[1]
  copy.set_stack("blueprint")
  copy.create_blueprint{
    surface = s, force = "player", area = area,
    include_entities = true, include_modules = true, include_station_names = true,
    include_trains = true, include_fuel = true,
  }
  if copy.is_blueprint_setup() then out.rebuilt = copy.export_stack() end
  inv.destroy()
  clear(s, area)
  return out
end

local function build_all(stack, path, builds)
  if stack.is_blueprint and stack.is_blueprint_setup() then
    builds[path] = build(stack)
  elseif stack.is_blueprint_book then
    local inv = stack.get_inventory(defines.inventory.item_main)
    for i = 1, #inv do
      if inv[i].valid_for_read then
        -- Children are addressed by their 0-based book index, as in the JSON.
        build_all(inv[i], (path == "" and "" or path .. "/") .. (i - 1), builds)
      end
    end
  end
end

-- Circuit tests: build one blueprint and keep it, powered by a global
-- electric network; inject signals with script-wired constant combinators
-- (no reach limit); read networks and logistic requests after some ticks.

local function powered(s)
  s.create_global_electric_network()
  if not (storage.power and storage.power.valid) then
    local at = {-1000, -1000}
    s.request_to_generate_chunks(at, 1)
    s.force_generate_chunk_requests()
    storage.power = s.create_entity{name = "electric-energy-interface", position = at, force = "player"}
  end
end

local function signal_id(name)
  for _, kind in pairs({"item", "fluid", "virtual_signal"}) do
    if prototypes[kind][name] then
      return {type = (kind == "virtual_signal") and "virtual" or kind, name = name, quality = "normal"}
    end
  end
end

local function signals_of(network)
  local out = {}
  for _, sig in pairs((network and network.signals) or {}) do
    local name = sig.signal.name
    if sig.signal.quality and sig.signal.quality ~= "normal" then name = name .. "@" .. sig.signal.quality end
    out[name] = sig.count
  end
  return out
end

local function circuit_teardown()
  local c = storage.circuit
  if not c then return end
  for _, inj in pairs(c.injectors) do
    if inj.valid then inj.destroy() end
  end
  clear(surface(), c.area)
  storage.circuit = nil
end

local function reply(t)
  rcon.print(helpers.table_to_json(t))
end

remote.add_interface("ghostwriter", {
  circuit_setup = function(text)
    circuit_teardown()
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local rc = stack.import_stack(text)
    if not (stack.valid_for_read and stack.is_blueprint) then
      inv.destroy()
      return reply{error = "not a blueprint (import_stack returned " .. rc .. ")"}
    end
    local s = surface()
    powered(s)
    local out, area, built = place(stack)
    inv.destroy()
    storage.circuit = {area = area, built = built, injectors = {}}
    local numbers = {}
    for n in pairs(built) do numbers[#numbers + 1] = n end
    out.built = numbers
    reply(out)
  end,

  circuit_inject = function(json)
    local args = helpers.json_to_table(json)
    local c = storage.circuit
    local target = c and c.built[args.entity]
    if not (target and target.valid) then return reply{error = "entity " .. args.entity .. " was not built"} end
    local connector_id = defines.wire_connector_id[args.connector]
    local target_connector = connector_id and target.get_wire_connector(connector_id, true)
    if not target_connector then return reply{error = target.name .. " has no connector " .. args.connector} end

    local slot = args.entity .. ":" .. args.connector
    local inj = c.injectors[slot]
    if not (inj and inj.valid) then
      local s = surface()
      local spot = s.find_non_colliding_position("constant-combinator", {c.area[2][1] + 2, c.area[1][2] + 2}, 64, 1)
      inj = s.create_entity{name = "constant-combinator", position = spot, force = "player"}
      local color = args.connector:find("green") and defines.wire_connector_id.circuit_green
                    or defines.wire_connector_id.circuit_red
      inj.get_wire_connector(color, true).connect_to(target_connector, false, defines.wire_origin.script)
      c.injectors[slot] = inj
    end
    local section = inj.get_control_behavior().get_section(1)
    for i = section.filters_count, 1, -1 do section.clear_slot(i) end
    local i = 0
    for name, count in pairs(args.signals or {}) do
      local id = signal_id(name)
      if not id then return reply{error = "unknown signal " .. name} end
      i = i + 1
      section.set_slot(i, {value = id, min = count})
    end
    reply{ok = true}
  end,

  circuit_read = function(json)
    local args = helpers.json_to_table(json)
    local c = storage.circuit
    local entity = c and c.built[args.entity]
    if not (entity and entity.valid) then return reply{error = "entity " .. args.entity .. " was not built"} end
    local out = {}
    if args.connector then
      local id = defines.wire_connector_id[args.connector]
      if not id then return reply{error = "no connector " .. args.connector} end
      out.signals = signals_of(entity.get_circuit_network(id))
    end
    local point = entity.type == "logistic-container"
      and entity.get_logistic_point(defines.logistic_member_index.logistic_container)
    if point then
      out.requests = {}
      for _, f in pairs(point.filters or {}) do
        if f.name and f.count ~= 0 then out.requests[f.name] = f.count end
      end
    end
    for k, v in pairs(defines.entity_status) do
      if v == entity.status then out.status = k end
    end
    reply(out)
  end,

  circuit_teardown = function()
    circuit_teardown()
    reply{ok = true}
  end,

  tick = function()
    rcon.print(game.tick)
  end,

  validate = function(text)
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local result = {import = stack.import_stack(text), builds = {}}
    if stack.valid_for_read then
      result.export = stack.export_stack()
      build_all(stack, "", result.builds)
    end
    inv.destroy()
    rcon.print(helpers.table_to_json(result))
  end,
  version = function()
    rcon.print(script.active_mods["base"])
  end,
})
