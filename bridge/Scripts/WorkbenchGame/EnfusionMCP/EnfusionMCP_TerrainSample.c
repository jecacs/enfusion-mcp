/**
 * SPDX-License-Identifier: MIT
 * Adapted JsonApiStruct/NetApiHandler patterns from the MIT-licensed upstream:
 * https://github.com/steffenbk/enfusion-mcp-BK
 * Upstream commit: 0acfa884228477043c6b4c2b8c7f0c270d648398
 * See the source repository NOTICE.md for complete attribution.
 *
 * Staged read-only batched terrain endpoint. UNVERIFIED_LIVE until ValidateScripts.
 * APIFunc: EnfusionMCP_TerrainSample
 */

class EnfusionMCP_TerrainPointRequest : JsonApiStruct
{
	float x;
	float z;

	void EnfusionMCP_TerrainPointRequest()
	{
		// A missing coordinate must not silently become the valid coordinate zero.
		x = 1000001.0;
		z = 1000001.0;
		RegV("x");
		RegV("z");
	}
}

class EnfusionMCP_TerrainSampleRequest : JsonApiStruct
{
	ref array<ref EnfusionMCP_TerrainPointRequest> points;

	void EnfusionMCP_TerrainSampleRequest()
	{
		points = {};
		RegV("points");
	}
}

class EnfusionMCP_TerrainSampleItem : JsonApiStruct
{
	float requestedX;
	float requestedZ;
	float terrainY;
	float normalX;
	float normalY;
	float normalZ;
	bool hasTerrain;

	void EnfusionMCP_TerrainSampleItem()
	{
		RegV("requestedX");
		RegV("requestedZ");
		RegV("terrainY");
		RegV("normalX");
		RegV("normalY");
		RegV("normalZ");
		RegV("hasTerrain");
	}
}

class EnfusionMCP_TerrainSampleResponse : JsonApiStruct
{
	string status;
	string errorCode;
	string message;
	string bridgeProtocolVersion;
	ref array<ref EnfusionMCP_TerrainSampleItem> m_aResults;
	ref array<string> m_aWarnings;

	void EnfusionMCP_TerrainSampleResponse()
	{
		RegV("status");
		RegV("errorCode");
		RegV("message");
		RegV("bridgeProtocolVersion");
		bridgeProtocolVersion = "enfusion-mcp-bridge-v1";
		m_aResults = {};
		m_aWarnings = {};
	}

	override void OnPack()
	{
		StartArray("results");
		for (int i = 0; i < m_aResults.Count(); i++)
			ItemObject(m_aResults[i]);
		EndArray();

		StartArray("warnings");
		for (int warningIndex = 0; warningIndex < m_aWarnings.Count(); warningIndex++)
			ItemString(m_aWarnings[warningIndex]);
		EndArray();
	}
}

class EnfusionMCP_TerrainSample : NetApiHandler
{
	static bool IsFiniteCoordinate(float value)
	{
		return value == value && Math.AbsFloat(value) <= 1000000.0;
	}

	static bool IsFiniteVector(vector value)
	{
		return IsFiniteCoordinate(value[0])
			&& IsFiniteCoordinate(value[1])
			&& IsFiniteCoordinate(value[2]);
	}

	static bool BoundsAreFiniteAndOrdered(vector boundsMin, vector boundsMax)
	{
		return IsFiniteVector(boundsMin)
			&& IsFiniteVector(boundsMax)
			&& boundsMin[0] <= boundsMax[0]
			&& boundsMin[1] <= boundsMax[1]
			&& boundsMin[2] <= boundsMax[2];
	}

	static void Fail(EnfusionMCP_TerrainSampleResponse response, string code, string text)
	{
		response.status = "error";
		response.errorCode = code;
		response.message = text;
	}

	override JsonApiStruct GetRequest()
	{
		return new EnfusionMCP_TerrainSampleRequest();
	}

	override JsonApiStruct GetResponse(JsonApiStruct request)
	{
		EnfusionMCP_TerrainSampleRequest typedRequest = EnfusionMCP_TerrainSampleRequest.Cast(request);
		EnfusionMCP_TerrainSampleResponse response = new EnfusionMCP_TerrainSampleResponse();
		if (!typedRequest || !typedRequest.points)
		{
			Fail(response, "INVALID_REQUEST", "points array is required");
			return response;
		}
		if (typedRequest.points.Count() < 1 || typedRequest.points.Count() > 1000)
		{
			Fail(response, "INVALID_CARDINALITY", "points count must be between 1 and 1000");
			return response;
		}

		for (int validationIndex = 0; validationIndex < typedRequest.points.Count(); validationIndex++)
		{
			EnfusionMCP_TerrainPointRequest requestedPoint = typedRequest.points[validationIndex];
			if (!requestedPoint || !IsFiniteCoordinate(requestedPoint.x) || !IsFiniteCoordinate(requestedPoint.z))
			{
				Fail(response, "INVALID_COORDINATE", "all X/Z values must be finite and bounded");
				return response;
			}
		}

		WorldEditor worldEditor = Workbench.GetModule(WorldEditor);
		if (!worldEditor)
		{
			Fail(response, "WORLD_EDITOR_NOT_RUNNING", "WorldEditor module is not running");
			return response;
		}
		WorldEditorAPI api = worldEditor.GetApi();
		if (!api)
		{
			Fail(response, "WORLD_EDITOR_API_UNAVAILABLE", "WorldEditorAPI is unavailable");
			return response;
		}

		vector boundsMin;
		vector boundsMax;
		if (!worldEditor.GetTerrainBounds(boundsMin, boundsMax))
		{
			Fail(response, "TERRAIN_UNAVAILABLE", "Terrain bounds are unavailable");
			return response;
		}
		if (!BoundsAreFiniteAndOrdered(boundsMin, boundsMax))
		{
			Fail(response, "INVALID_TERRAIN_BOUNDS", "Terrain bounds are non-finite or unordered");
			return response;
		}

		BaseWorld world = api.GetWorld();
		if (!world)
		{
			Fail(response, "WORLD_UNAVAILABLE", "WorldEditor world is unavailable");
			return response;
		}
		for (int i = 0; i < typedRequest.points.Count(); i++)
		{
			EnfusionMCP_TerrainPointRequest inputPoint = typedRequest.points[i];
			EnfusionMCP_TerrainSampleItem item = new EnfusionMCP_TerrainSampleItem();
			item.requestedX = inputPoint.x;
			item.requestedZ = inputPoint.z;
			item.hasTerrain = false;

			if (inputPoint.x >= boundsMin[0] && inputPoint.x <= boundsMax[0]
				&& inputPoint.z >= boundsMin[2] && inputPoint.z <= boundsMax[2])
			{
				float terrainY;
				if (api.TryGetTerrainSurfaceY(inputPoint.x, inputPoint.z, terrainY)
					&& IsFiniteCoordinate(terrainY))
				{
					vector normalPosition = Vector(inputPoint.x, terrainY, inputPoint.z);
					vector normal = SCR_TerrainHelper.GetTerrainNormal(normalPosition, world);
					float normalLength = Math.Sqrt(
						normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]
					);
					if (IsFiniteVector(normalPosition)
						&& Math.AbsFloat(normalPosition[1] - terrainY) <= 0.05
						&& IsFiniteVector(normal)
						&& IsFiniteCoordinate(normalLength)
						&& normalLength > 0.000001)
					{
						float normalizedX = normal[0] / normalLength;
						float normalizedY = normal[1] / normalLength;
						float normalizedZ = normal[2] / normalLength;
						if (IsFiniteCoordinate(normalizedX)
							&& IsFiniteCoordinate(normalizedY)
							&& IsFiniteCoordinate(normalizedZ))
						{
							item.terrainY = terrainY;
							item.normalX = normalizedX;
							item.normalY = normalizedY;
							item.normalZ = normalizedZ;
							item.hasTerrain = true;
						}
					}
				}
			}
			response.m_aResults.Insert(item);
		}

		if (response.m_aResults.Count() != typedRequest.points.Count())
		{
			Fail(response, "CARDINALITY_MISMATCH", "result order/cardinality could not be preserved");
			return response;
		}
		response.status = "ok";
		response.errorCode = "";
		response.message = "Terrain sampled without changing Workbench state";
		return response;
	}
}
