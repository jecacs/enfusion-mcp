/**
 * SPDX-License-Identifier: MIT
 * Adapted JsonApiStruct/NetApiHandler patterns from the MIT-licensed upstream:
 * https://github.com/steffenbk/enfusion-mcp-BK
 * Upstream commit: 0acfa884228477043c6b4c2b8c7f0c270d648398
 * See the source repository NOTICE.md for complete attribution.
 *
 * Staged read-only bridge endpoint. UNVERIFIED_LIVE until ValidateScripts.
 * APIFunc: EnfusionMCP_GetContext
 */

class EnfusionMCP_ContextVec3 : JsonApiStruct
{
	float x;
	float y;
	float z;

	void EnfusionMCP_ContextVec3()
	{
		RegV("x");
		RegV("y");
		RegV("z");
	}
}

class EnfusionMCP_ContextTerrainBounds : JsonApiStruct
{
	float minX;
	float minY;
	float minZ;
	float maxX;
	float maxY;
	float maxZ;

	void EnfusionMCP_ContextTerrainBounds()
	{
		RegV("minX");
		RegV("minY");
		RegV("minZ");
		RegV("maxX");
		RegV("maxY");
		RegV("maxZ");
	}
}

class EnfusionMCP_GetContextRequest : JsonApiStruct
{
	void EnfusionMCP_GetContextRequest()
	{
		// No request fields. The Python MCP boundary rejects extra arguments.
	}
}

class EnfusionMCP_GetContextResponse : JsonApiStruct
{
	string status;
	string errorCode;
	string message;
	string bridgeProtocolVersion;
	string bridgeBuildId;
	string catalogHash;
	string worldPath;
	string mode;
	int currentSubscene;
	int currentLayerId;
	string activeLayerPath;
	ref EnfusionMCP_ContextTerrainBounds terrainBounds;
	int selectionCount;
	string selectedName;
	string selectedClass;
	bool polygonCompatible;
	bool shapeClosed;
	ref array<ref EnfusionMCP_ContextVec3> m_aShapePointsWorld;

	void EnfusionMCP_GetContextResponse()
	{
		RegV("status");
		RegV("errorCode");
		RegV("message");
		RegV("bridgeProtocolVersion");
		RegV("bridgeBuildId");
		RegV("catalogHash");
		RegV("worldPath");
		RegV("mode");
		RegV("currentSubscene");
		RegV("currentLayerId");
		RegV("activeLayerPath");
		terrainBounds = new EnfusionMCP_ContextTerrainBounds();
		RegV("terrainBounds");
		RegV("selectionCount");
		RegV("selectedName");
		RegV("selectedClass");
		RegV("polygonCompatible");
		RegV("shapeClosed");
		m_aShapePointsWorld = {};
		currentSubscene = -1;
		currentLayerId = -1;
		mode = "unknown";
		bridgeProtocolVersion = "enfusion-mcp-bridge-v1";
		bridgeBuildId = "enfusion-mcp-bridge-v1-map-agnostic";
		catalogHash = "fe7a5214aebc0cfc171d5b0b218fddeb43c75303a5ae94cc77457c8b7334ee06";
	}

	override void OnPack()
	{
		StartArray("shapePointsWorld");
		for (int i = 0; i < m_aShapePointsWorld.Count(); i++)
			ItemObject(m_aShapePointsWorld[i]);
		EndArray();
	}
}

class EnfusionMCP_GetContext : NetApiHandler
{
	static void Fail(EnfusionMCP_GetContextResponse response, string code, string text)
	{
		response.status = "error";
		response.errorCode = code;
		response.message = text;
	}

	override JsonApiStruct GetRequest()
	{
		return new EnfusionMCP_GetContextRequest();
	}

	override JsonApiStruct GetResponse(JsonApiStruct request)
	{
		EnfusionMCP_GetContextResponse response = new EnfusionMCP_GetContextResponse();
		WorldEditor worldEditor = Workbench.GetModule(WorldEditor);
		if (!worldEditor)
		{
			response.mode = "no_world_editor";
			Fail(response, "WORLD_EDITOR_NOT_RUNNING", "WorldEditor module is not running");
			return response;
		}

		WorldEditorAPI api = worldEditor.GetApi();
		if (!api)
		{
			response.mode = "game";
			Fail(response, "WORLD_EDITOR_API_UNAVAILABLE", "WorldEditorAPI is unavailable");
			return response;
		}

		api.GetWorldPath(response.worldPath);
		response.currentSubscene = api.GetCurrentSubScene();
		response.currentLayerId = api.GetCurrentEntityLayerId();
		response.activeLayerPath = api.GetSubsceneLayerPath(
			response.currentSubscene,
			response.currentLayerId
		);

		if (api.IsGameMode())
			response.mode = "game";
		else if (api.IsPrefabEditMode())
			response.mode = "prefab";
		else
			response.mode = "edit";

		vector boundsMin;
		vector boundsMax;
		if (!worldEditor.GetTerrainBounds(boundsMin, boundsMax))
		{
			Fail(response, "TERRAIN_UNAVAILABLE", "Terrain bounds are unavailable");
			return response;
		}
		response.terrainBounds.minX = boundsMin[0];
		response.terrainBounds.minY = boundsMin[1];
		response.terrainBounds.minZ = boundsMin[2];
		response.terrainBounds.maxX = boundsMax[0];
		response.terrainBounds.maxY = boundsMax[1];
		response.terrainBounds.maxZ = boundsMax[2];

		response.selectionCount = api.GetSelectedEntitiesCount();
		if (response.selectionCount == 1)
		{
			IEntitySource selectedSource = api.GetSelectedEntity(0);
			if (selectedSource)
			{
				response.selectedName = selectedSource.GetName();
				response.selectedClass = selectedSource.GetClassName();
				IEntity selectedEntity = api.SourceToEntity(selectedSource);
				PolylineShapeEntity polygon = PolylineShapeEntity.Cast(selectedEntity);
				if (polygon)
				{
					response.polygonCompatible = true;
					response.shapeClosed = polygon.IsClosed();
					array<vector> localPoints = {};
					polygon.GetPointsPositions(localPoints);
					if (localPoints.Count() > 1024)
					{
						Fail(response, "SHAPE_TOO_COMPLEX", "Selected Shape exceeds 1024 points");
						return response;
					}

					for (int pointIndex = 0; pointIndex < localPoints.Count(); pointIndex++)
					{
						vector worldPoint = polygon.CoordToParent(localPoints[pointIndex]);
						EnfusionMCP_ContextVec3 point = new EnfusionMCP_ContextVec3();
						point.x = worldPoint[0];
						point.y = worldPoint[1];
						point.z = worldPoint[2];
						response.m_aShapePointsWorld.Insert(point);
					}
				}
			}
		}

		response.status = "ok";
		response.errorCode = "";
		response.message = "Context read without changing Workbench state";
		return response;
	}
}
